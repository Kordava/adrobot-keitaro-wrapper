"""Keitaro Admin API transport with read-only validation and guarded one-shot writes."""

from typing import NoReturn

import httpx
from pydantic import ValidationError

from adrobot.keitaro.create_models import (
    ActiveOffer,
    CampaignGroup,
    CampaignReadback,
    CreatedEntity,
    ReferenceItem,
)
from adrobot.keitaro.exceptions import (
    KeitaroContractError,
    KeitaroNotFound,
    KeitaroRateLimited,
    KeitaroUnauthorized,
    KeitaroUnavailable,
    KeitaroUpstreamValidationError,
    KeitaroWriteError,
)
from adrobot.keitaro.read_models import CampaignStream, parse_streams, validate_campaign_id


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("Nonstandard JSON constant")


class KeitaroClient:
    """Handle Keitaro communication and validation, not application business logic."""

    def __init__(self, transport: httpx.AsyncClient) -> None:
        self._transport = transport

    async def get_campaign_streams(self, campaign_id: int) -> list[CampaignStream]:
        """Read and validate *all* streams for a campaign; never mutate remotely.

        The instance was observed returning a JSON array containing nested
        `offers[]` bindings. The transport must be configured with an `Api-Key`
        header by its owner; keys are never accepted from HTTP callers.
        """
        validate_campaign_id(campaign_id)
        try:
            response = await self._transport.get(f"/admin_api/v1/campaigns/{campaign_id}/streams")
        except httpx.TimeoutException as exc:
            raise KeitaroUnavailable(
                "Remote tracker is unavailable", failure_kind="timeout"
            ) from exc
        except httpx.TransportError as exc:
            raise KeitaroUnavailable(
                "Remote tracker is unavailable", failure_kind="network"
            ) from exc

        if response.status_code in (401, 403):
            raise KeitaroUnauthorized(
                "Keitaro refused API authorization", upstream_status=response.status_code
            )
        if response.status_code == 404:
            raise KeitaroNotFound("Campaign does not exist", upstream_status=response.status_code)
        if response.status_code == 422:
            raise KeitaroUpstreamValidationError(
                "Keitaro rejected remote input", upstream_status=response.status_code
            )
        if response.status_code == 429:
            raise KeitaroRateLimited("Keitaro rate limit", upstream_status=response.status_code)
        if response.status_code >= 300:
            raise KeitaroUnavailable(
                "Keitaro rejected the request", upstream_status=response.status_code
            )
        if response.status_code != 200:
            raise KeitaroContractError(
                "Keitaro returned an unexpected status", upstream_status=response.status_code
            )
        if "content-range" in response.headers or any(
            "next" in link.get("rel", "").split() for link in response.links.values()
        ):
            raise KeitaroContractError(
                "Keitaro response completeness is unconfirmed",
                failure_kind="completeness",
                upstream_status=response.status_code,
            )
        try:
            payload: object = response.json(parse_constant=_reject_constant)
        except ValueError as exc:
            raise KeitaroContractError(
                "Keitaro returned invalid JSON",
                failure_kind="json",
                failure_stage="decode_json",
                upstream_status=response.status_code,
            ) from exc
        try:
            return parse_streams(campaign_id, payload)
        except KeitaroContractError as exc:
            exc.upstream_status = response.status_code
            raise

    async def _get_create_json(self, path: str) -> object:
        """Strict GET for Create preflight/readback; never return an incomplete list."""
        try:
            response = await self._transport.get(path)
        except httpx.TimeoutException as exc:
            raise KeitaroUnavailable(
                "Remote tracker is unavailable", failure_kind="timeout"
            ) from exc
        except httpx.TransportError as exc:
            raise KeitaroUnavailable(
                "Remote tracker is unavailable", failure_kind="network"
            ) from exc
        if response.status_code in (401, 403):
            raise KeitaroUnauthorized("Keitaro refused API authorization")
        if response.status_code == 404:
            raise KeitaroNotFound("Remote resource does not exist")
        if response.status_code == 422:
            raise KeitaroUpstreamValidationError("Keitaro rejected remote input")
        if response.status_code == 429:
            raise KeitaroRateLimited("Keitaro rate limit")
        if response.status_code != 200:
            raise KeitaroUnavailable("Remote tracker is unavailable")
        if "content-range" in response.headers or any(
            "next" in link.get("rel", "").split() for link in response.links.values()
        ):
            raise KeitaroContractError("Remote response completeness is unconfirmed")
        try:
            return response.json(parse_constant=_reject_constant)
        except ValueError as exc:
            raise KeitaroContractError("Remote tracker returned invalid JSON") from exc

    async def get_offer(self, offer_id: int) -> ActiveOffer:
        """Read a single existing global offer (never create an offer)."""
        validate_campaign_id(offer_id)
        data = await self._get_create_json(f"/admin_api/v1/offers/{offer_id}")
        try:
            offer = ActiveOffer.model_validate(data)
        except ValidationError as exc:
            raise KeitaroContractError("Remote offer has an invalid shape") from exc
        if offer.id != offer_id:
            raise KeitaroContractError("Remote offer identifiers disagree")
        return offer

    async def list_offers(self) -> list[ActiveOffer]:
        """Full read-only offer listing; reject ambiguous or inconsistent arrays."""
        data = await self._get_create_json("/admin_api/v1/offers")
        if not isinstance(data, list) or len(data) > 20000:
            raise KeitaroContractError("Remote offer listing is invalid or too large")
        try:
            offers = [ActiveOffer.model_validate(item) for item in data]
        except ValidationError as exc:
            raise KeitaroContractError("Remote offer listing has invalid entries") from exc
        if len({item.id for item in offers}) != len(offers):
            raise KeitaroContractError("Remote offer listing has repeated identifiers")
        return offers

    async def list_campaign_groups(self) -> list[CampaignGroup]:
        """Read the complete campaign-group catalog using its own wire contract."""
        data = await self._get_create_json("/admin_api/v1/groups?type=campaigns")
        if not isinstance(data, list) or len(data) > 20000:
            raise KeitaroContractError("Remote campaign group listing is invalid")
        try:
            groups = [CampaignGroup.model_validate(item) for item in data]
        except ValidationError as exc:
            raise KeitaroContractError("Remote campaign group listing has invalid entries") from exc
        if len({item.id for item in groups}) != len(groups):
            raise KeitaroContractError("Remote campaign group listing has duplicate IDs")
        return groups

    async def list_create_references(self, resource: str) -> list[ReferenceItem]:
        """Read one configured default category, never accept an arbitrary path."""
        if resource not in ("domains", "traffic_sources"):
            raise ValueError("Unsupported Create default category")
        data = await self._get_create_json(f"/admin_api/v1/{resource}")
        if not isinstance(data, list) or len(data) > 20000:
            raise KeitaroContractError("Remote reference listing is invalid")
        try:
            items = [ReferenceItem.model_validate(item) for item in data]
        except ValidationError as exc:
            raise KeitaroContractError("Remote reference listing has invalid entries") from exc
        if len({item.id for item in items}) != len(items):
            raise KeitaroContractError("Remote reference listing has duplicate IDs")
        return items

    async def get_create_capabilities(self) -> tuple[object, object]:
        """Read filter/action catalogs to guard country and redirect assumptions."""
        return (
            await self._get_create_json("/admin_api/v1/stream_filters"),
            await self._get_create_json("/admin_api/v1/streams_actions"),
        )

    async def get_campaign_for_create(self, campaign_id: int) -> CampaignReadback:
        """Get private campaign attributes for final verification."""
        validate_campaign_id(campaign_id)
        data = await self._get_create_json(f"/admin_api/v1/campaigns/{campaign_id}")
        try:
            campaign = CampaignReadback.model_validate(data)
        except ValidationError as exc:
            raise KeitaroContractError("Remote campaign has an invalid shape") from exc
        if campaign.id != campaign_id:
            raise KeitaroContractError("Remote campaign identifiers disagree")
        return campaign

    async def _post_create(self, resource: str, payload: dict[str, object]) -> CreatedEntity:
        """Attempt one POST. A lost or unparseable reply is an UNKNOWN outcome.

        No retries, fallback POSTs, rollback, or redirects. Never expose payloads
        or upstream response text to callers/logs.
        """
        if resource not in ("campaigns", "streams"):
            raise ValueError("Unsupported Create resource")
        try:
            response = await self._transport.post(f"/admin_api/v1/{resource}", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise KeitaroWriteError(ambiguous=True, category="network") from exc
        # Only documented, definitive client errors are treated as rejection.
        # A 202, redirect, rate limit, timeout or 5xx might follow a committed
        # mutation; never invite a blind retry for these uncertain outcomes.
        if response.status_code not in (200, 201):
            definite_rejection = response.status_code in {
                400,
                401,
                403,
                404,
                405,
                406,
                413,
                415,
                422,
            }
            raise KeitaroWriteError(
                ambiguous=not definite_rejection,
                category="remote_rejected" if definite_rejection else "remote_failure",
            )
        try:
            raw = response.json(parse_constant=_reject_constant)
            created = CreatedEntity.model_validate(raw)
        except (ValueError, ValidationError) as exc:
            raise KeitaroWriteError(ambiguous=True, category="invalid_reply") from exc
        return created

    async def post_campaign(self, payload: dict[str, object]) -> CreatedEntity:
        """One possible campaign write. Called only by the guarded Create service."""
        return await self._post_create("campaigns", payload)

    async def post_stream(self, payload: dict[str, object]) -> CreatedEntity:
        """One possible stream write. Called only by the guarded Create service."""
        return await self._post_create("streams", payload)
