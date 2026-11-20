"""One-shot campaign creation and independently verified Keitaro readback.

Each stage may write at most once per incoming request. A known campaign ID is
*not* proof of a finished campaign. We never compensate, retry, or reconstruct
an ambiguous operation from a client-side guess.
"""

import logging
import secrets
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from adrobot.api.create_models import (
    CampaignCreateInput,
    CreateSuccess,
    ReferenceData,
    SafeOffer,
)
from adrobot.config import Settings
from adrobot.keitaro.client import KeitaroClient
from adrobot.keitaro.create_models import CampaignReadback, ReferenceItem
from adrobot.keitaro.exceptions import (
    KeitaroContractError,
    KeitaroError,
    KeitaroNotFound,
    KeitaroWriteError,
)
from adrobot.keitaro.read_models import JS_SAFE_INTEGER, CampaignStream

logger = logging.getLogger("adrobot.create")
GOOGLE_REDIRECT = "https://google.com"
# A parsed POST reply is evidence of a response, not yet full remote readback.
ConfirmedStage = Literal[
    "none",
    "campaign_post_accepted",
    "campaign_readback_verified",
    "geo_post_accepted",
    "fallback_post_accepted",
]


class CreateUnavailable(Exception):
    """No Create authorization/mapping/defaults; never an upstream write."""

    code = "create_unavailable"
    status_code = 503

    def __init__(self, message: str = "Create is not configured or authorized") -> None:
        super().__init__(message)


class CreateRejected(Exception):
    """The first remote write was definitively rejected without a known ID."""

    code = "create_rejected"
    status_code = 502

    def __init__(self) -> None:
        super().__init__("Keitaro rejected the Create request")


class InvalidOffer(Exception):
    """A supplied global offer is absent or cannot be used."""

    code = "invalid_offer"
    status_code = 422


class CreateProblem(Exception):
    """Safe, explicit non-success *after* a potential upstream mutation."""

    def __init__(
        self,
        *,
        status: Literal["PARTIAL", "UNKNOWN"],
        campaign_id: int | None,
        stage: str,
        last_confirmed_stage: ConfirmedStage,
        category: str,
    ) -> None:
        super().__init__("Campaign creation was not verified. Inspect Keitaro before retrying.")
        self.status = status
        self.campaign_id = campaign_id
        self.stage = stage  # Operation that was attempted or being checked.
        self.last_confirmed_stage = last_confirmed_stage  # Last positive response/readback.
        self.category = category
        self.code = "create_partial" if status == "PARTIAL" else "create_unknown"
        self.status_code = 409 if status == "PARTIAL" else 502


@dataclass(frozen=True)
class CreateDefaults:
    domain_id: int
    group_id: int
    traffic_source_id: int
    offer_action_type: str
    domain_name: str
    group_name: str
    traffic_source_name: str


class CampaignCreator:
    """Create service; adapter owns wire I/O, service owns semantic comparisons."""

    def __init__(self, api: KeitaroClient, settings: Settings) -> None:
        self.api = api
        self.settings = settings

    def _check_settings(self, *, require_write: bool) -> None:
        if not self.settings.create_defaults_configured:
            raise CreateUnavailable("Create defaults have not been configured")
        if not self.settings.create_mapping_verified:
            raise CreateUnavailable("Keitaro Create mapping has not been confirmed")
        if require_write and not self.settings.create_writes_enabled:
            raise CreateUnavailable("Keitaro Create writes are not enabled")

    async def validated_defaults(self, *, require_write: bool = False) -> CreateDefaults:
        """Verify *all three* actual defaults, plus GEO/action capability, before POST."""
        self._check_settings(require_write=require_write)
        config = self.settings
        assert config.create_domain_id is not None
        assert config.create_group_id is not None
        assert config.create_traffic_source_id is not None
        assert config.create_offer_action_type is not None
        pairs = (
            ("domains", config.create_domain_id),
            ("traffic_sources", config.create_traffic_source_id),
        )
        selected: list[ReferenceItem] = []
        for resource, wanted_id in pairs:
            options = await self.api.list_create_references(resource)
            matching = [item for item in options if item.id == wanted_id]
            if len(matching) != 1 or matching[0].state != "active":
                raise CreateUnavailable("Configured Create default is unavailable")
            selected.append(matching[0])
        groups = await self.api.list_campaign_groups()
        group = next((item for item in groups if item.id == config.create_group_id), None)
        if group is None:
            raise CreateUnavailable("Configured Create default is unavailable")
        filters, actions = await self.api.get_create_capabilities()
        if not isinstance(filters, list) or not isinstance(actions, list):
            raise KeitaroContractError("Remote Create capability catalog has an invalid shape")
        country_available = any(
            isinstance(item, dict)
            and item.get("value") == "country"
            and isinstance(item.get("modes"), dict)
            and "accept" in item["modes"]
            for item in filters
        )
        actions_available = {
            item.get("key")
            for item in actions
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }
        if not country_available or "http" not in actions_available:
            raise CreateUnavailable("Required GEO filter or redirect action is unavailable")
        if config.create_offer_action_type not in actions_available:
            raise CreateUnavailable("Configured offer-flow action is unavailable")
        return CreateDefaults(
            domain_id=config.create_domain_id,
            group_id=config.create_group_id,
            traffic_source_id=config.create_traffic_source_id,
            offer_action_type=config.create_offer_action_type,
            domain_name=selected[0].name,
            group_name=group.name,
            traffic_source_name=selected[1].name,
        )

    async def reference_data(self) -> ReferenceData:
        """Safe labels only, no raw upstream records or credential values."""
        try:
            refs = await self.validated_defaults()
        except (CreateUnavailable, KeitaroError):
            return ReferenceData(available=False, message="Create defaults are not available")
        return ReferenceData(
            available=True,
            message="Create references are valid; remote writes require separate authorization",
            domain=refs.domain_name,
            group=refs.group_name,
            traffic_source=refs.traffic_source_name,
        )

    async def search_offers(self, query: str) -> list[SafeOffer]:
        """Safe exact-ID or name lookup; never silently truncate upstream data."""
        if not query.strip() or len(query) > 100:
            return []
        cleaned = query.strip()
        if cleaned.isascii() and cleaned.isdecimal():
            wanted = int(cleaned)
            if not 1 <= wanted <= JS_SAFE_INTEGER:
                return []
            try:
                offer = await self.api.get_offer(wanted)
            except KeitaroNotFound:
                return []
            return [SafeOffer(id=offer.id, name=offer.name)] if offer.state == "active" else []
        offers = await self.api.list_offers()
        needle = cleaned.casefold()
        return [
            SafeOffer(id=offer.id, name=offer.name)
            for offer in offers
            if offer.state == "active" and needle in offer.name.casefold()
        ][:20]

    @staticmethod
    def _assert_campaign(
        remote: CampaignReadback, *, campaign_id: int, name: str, alias: str, refs: CreateDefaults
    ) -> None:
        if (
            remote.id != campaign_id
            or remote.name != name
            or remote.alias != alias
            or remote.state != "active"
            or remote.type != "position"
            or remote.domain_id != refs.domain_id
            or remote.group_id != refs.group_id
            or remote.traffic_source_id != refs.traffic_source_id
        ):
            raise ValueError("Created campaign attributes differ from requested state")

    @staticmethod
    def _assert_streams(
        streams: list[CampaignStream],
        *,
        geo_id: int,
        fallback_id: int,
        countries: list[str],
        offer_id: int,
        offer_action_type: str,
    ) -> None:
        if len(streams) != 2 or len({stream.id for stream in streams}) != 2:
            raise ValueError("Expected exactly two created streams")
        by_id = {stream.id: stream for stream in streams}
        if set(by_id) != {geo_id, fallback_id}:
            raise ValueError("Created stream identifiers differ from readback")
        geo = by_id[geo_id]
        fallback = by_id[fallback_id]
        if (
            geo.schema_name != "redirect"
            or geo.state != "active"
            or getattr(geo, "type", None) != "regular"
            or geo.position != 1
            or getattr(geo, "action_type", None) != "http"
            or getattr(geo, "action_payload", None) != GOOGLE_REDIRECT
            or geo.offers
            or len(geo.filters) != 1
        ):
            raise ValueError("GEO redirect attributes are inconsistent")
        country_filter = geo.filters[0]
        if (
            country_filter.get("name") != "country"
            or country_filter.get("mode") != "accept"
            or country_filter.get("payload") != countries
        ):
            raise ValueError("GEO filter does not match the requested countries")
        if (
            fallback.schema_name != "landings"
            or fallback.state != "active"
            or getattr(fallback, "type", None) != "default"
            or fallback.position != 2
            or getattr(fallback, "action_type", None) != offer_action_type
            or fallback.filters
            or getattr(fallback, "landings", None) != []
            or len(fallback.offers) != 1
            or fallback.offers[0].offer_id != offer_id
            or fallback.offers[0].state != "active"
            or fallback.offers[0].share != 100
        ):
            raise ValueError("Fallback offer binding is inconsistent")
        # The default flow is evaluated only if the regular GEO flow rejects.

    async def create(self, submission: CampaignCreateInput, *, request_id: str) -> CreateSuccess:
        """Create and read back once; no rollback and no automatic retries."""
        started = perf_counter()
        stage = "preflight"
        last_confirmed_stage: ConfirmedStage = "none"
        campaign_id: int | None = None
        geo_id: int | None = None
        fallback_id: int | None = None
        try:
            refs = await self.validated_defaults(require_write=True)
            try:
                offer = await self.api.get_offer(submission.offer_id)
            except KeitaroError as exc:
                # A remote 404 is a user-visible invalid Offer ID; other GET
                # failures remain upstream errors and cannot trigger writes.
                if isinstance(exc, KeitaroNotFound):
                    raise InvalidOffer("Selected offer does not exist") from exc
                raise
            if offer.state != "active":
                raise InvalidOffer("Selected offer is not active")

            # The alias is a new server-generated identifier. Do not derive it
            # from user input or reuse an old campaign as a fallback.
            alias = "adrobot-" + secrets.token_hex(8)
            stage = "create_campaign"
            try:
                created = await self.api.post_campaign(
                    {
                        "name": submission.name,
                        "alias": alias,
                        "type": "position",
                        "state": "active",
                        "domain_id": refs.domain_id,
                        "group_id": str(refs.group_id),
                        "traffic_source_id": refs.traffic_source_id,
                    }
                )
            except KeitaroWriteError as exc:
                if not exc.ambiguous:
                    raise CreateRejected() from exc
                raise CreateProblem(
                    status="UNKNOWN",
                    campaign_id=None,
                    stage=stage,
                    last_confirmed_stage=last_confirmed_stage,
                    category=exc.category,
                ) from exc
            campaign_id = created.id
            last_confirmed_stage = "campaign_post_accepted"
            # Check rotation before additional writes; wrong type is a known
            # partial state and must not generate GEO/offer streams.
            stage = "campaign_created"
            if getattr(created, "type", "position") not in ("position", None):
                raise CreateProblem(
                    status="PARTIAL",
                    campaign_id=campaign_id,
                    stage=stage,
                    last_confirmed_stage=last_confirmed_stage,
                    category="campaign_rotation_mismatch",
                )
            try:
                early_campaign = await self.api.get_campaign_for_create(campaign_id)
            except KeitaroError as exc:
                raise CreateProblem(
                    status="UNKNOWN",
                    campaign_id=campaign_id,
                    stage=stage,
                    last_confirmed_stage=last_confirmed_stage,
                    category="readback_unavailable",
                ) from exc
            try:
                self._assert_campaign(
                    early_campaign,
                    campaign_id=campaign_id,
                    name=submission.name,
                    alias=alias,
                    refs=refs,
                )
            except ValueError as exc:
                raise CreateProblem(
                    status="PARTIAL",
                    campaign_id=campaign_id,
                    stage=stage,
                    last_confirmed_stage=last_confirmed_stage,
                    category="campaign_mismatch",
                ) from exc

            last_confirmed_stage = "campaign_readback_verified"
            stage = "create_geo_redirect"
            try:
                geo_reply = await self.api.post_stream(
                    {
                        "campaign_id": campaign_id,
                        "name": "Adrobot GEO redirect",
                        "type": "regular",
                        "schema": "redirect",
                        "position": 1,
                        "state": "active",
                        "action_type": "http",
                        "action_payload": GOOGLE_REDIRECT,
                        "filters": [
                            {"name": "country", "mode": "accept", "payload": submission.countries}
                        ],
                    }
                )
            except KeitaroWriteError as exc:
                raise CreateProblem(
                    status="UNKNOWN" if exc.ambiguous else "PARTIAL",
                    campaign_id=campaign_id,
                    stage=stage,
                    last_confirmed_stage=last_confirmed_stage,
                    category=exc.category,
                ) from exc
            geo_id = geo_reply.id
            last_confirmed_stage = "geo_post_accepted"

            stage = "create_offer_fallback"
            try:
                offer_reply = await self.api.post_stream(
                    {
                        "campaign_id": campaign_id,
                        "name": "Adrobot offer fallback",
                        "type": "default",
                        "schema": "landings",
                        "position": 2,
                        "state": "active",
                        "action_type": refs.offer_action_type,
                        "offers": [
                            {"offer_id": submission.offer_id, "share": 100, "state": "active"}
                        ],
                    }
                )
            except KeitaroWriteError as exc:
                raise CreateProblem(
                    status="UNKNOWN" if exc.ambiguous else "PARTIAL",
                    campaign_id=campaign_id,
                    stage=stage,
                    last_confirmed_stage=last_confirmed_stage,
                    category=exc.category,
                ) from exc
            fallback_id = offer_reply.id
            last_confirmed_stage = "fallback_post_accepted"

            stage = "readback"
            try:
                remote = await self.api.get_campaign_for_create(campaign_id)
                streams = await self.api.get_campaign_streams(campaign_id)
            except KeitaroError as exc:
                raise CreateProblem(
                    status="UNKNOWN",
                    campaign_id=campaign_id,
                    stage=stage,
                    last_confirmed_stage=last_confirmed_stage,
                    category="readback_unavailable",
                ) from exc
            try:
                self._assert_campaign(
                    remote, campaign_id=campaign_id, name=submission.name, alias=alias, refs=refs
                )
                self._assert_streams(
                    streams,
                    geo_id=geo_id,
                    fallback_id=fallback_id,
                    countries=submission.countries,
                    offer_id=submission.offer_id,
                    offer_action_type=refs.offer_action_type,
                )
            except ValueError as exc:
                raise CreateProblem(
                    status="PARTIAL",
                    campaign_id=campaign_id,
                    stage=stage,
                    last_confirmed_stage=last_confirmed_stage,
                    category="readback_mismatch",
                ) from exc
            logger.info(
                "campaign create verified",
                extra={
                    "action": "create_campaign",
                    "stage": "complete",
                    "request_id": request_id,
                    "campaign_id": campaign_id,
                    "stream_ids": [geo_id, fallback_id],
                    "duration_ms": round((perf_counter() - started) * 1000, 1),
                },
            )
            return CreateSuccess(
                campaign_id=campaign_id, editor_url=f"/editor?campaign_id={campaign_id}"
            )
        except CreateProblem as exc:
            logger.warning(
                "campaign create not verified",
                extra={
                    "action": "create_campaign",
                    "request_id": request_id,
                    "stage": exc.stage,
                    "last_confirmed_stage": exc.last_confirmed_stage,
                    "campaign_id": exc.campaign_id,
                    "error_code": exc.code,
                    "failure_kind": exc.category,
                    "duration_ms": round((perf_counter() - started) * 1000, 1),
                },
            )
            raise
