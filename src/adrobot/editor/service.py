"""Preflight of the entire remote campaign before touching the archive."""

from dataclasses import dataclass

from adrobot.keitaro.client import KeitaroClient
from adrobot.keitaro.read_models import CampaignStream


class EditorProblem(Exception):
    """Expected editor failure with a non-sensitive HTTP error contract."""

    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class RemoteCampaign:
    """Complete validated reads and confirmed names; ready for one SQL transaction."""

    campaign_id: int
    streams: list[CampaignStream]
    names: dict[int, str]


async def read_remote_campaign(upstream: KeitaroClient, campaign_id: int) -> RemoteCampaign:
    """Validate binding identity and resolve names before any archive writes.

    This policy belongs only to the editor. The B0 diagnostic endpoint continues
    to return the raw duplicate logical offers without deduplicating them.
    """
    streams = await upstream.get_campaign_streams(campaign_id)
    needed: set[int] = set()
    for stream in streams:
        if stream.schema_name != "landings":
            continue
        ids: set[int] = set()
        for binding in stream.offers:
            if binding.offer_id in ids:
                raise EditorProblem(
                    "editor_duplicate_offer_id",
                    "This campaign contains multiple bindings for the same offer in one stream; "
                    "the editor archive was not changed.",
                )
            ids.add(binding.offer_id)
            if binding.state not in ("active", "disabled"):
                raise EditorProblem(
                    "editor_binding_state_unrecognized",
                    "A remote offer binding has an unsupported state",
                )
            needed.add(binding.offer_id)

    names: dict[int, str] = {}
    if needed:
        catalog = await upstream.list_offers()
        names = {offer.id: offer.name.strip() for offer in catalog if offer.id in needed}
        for offer_id in sorted(needed - names.keys()):
            offer = await upstream.get_offer(offer_id)
            names[offer_id] = offer.name.strip()
        if any(not names.get(offer_id) for offer_id in needed):
            raise EditorProblem(
                "editor_offer_name_unavailable", "Remote offer names are incomplete"
            )
    return RemoteCampaign(campaign_id=campaign_id, streams=streams, names=names)
