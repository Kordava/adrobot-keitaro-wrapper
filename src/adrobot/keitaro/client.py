"""Read-only Keitaro Admin API transport with strict response contracts."""

import httpx

from adrobot.keitaro.exceptions import (
    KeitaroContractError,
    KeitaroNotFound,
    KeitaroRateLimited,
    KeitaroUnauthorized,
    KeitaroUnavailable,
)
from adrobot.keitaro.read_models import CampaignStream, parse_streams


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
        if campaign_id <= 0:
            raise ValueError("campaign_id must be positive")
        try:
            response = await self._transport.get(f"/admin_api/v1/campaigns/{campaign_id}/streams")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise KeitaroUnavailable("Remote tracker is unavailable") from exc

        if response.status_code in (401, 403):
            raise KeitaroUnauthorized("Keitaro refused API authorization")
        if response.status_code == 404:
            raise KeitaroNotFound("Campaign does not exist")
        if response.status_code == 429:
            raise KeitaroRateLimited("Keitaro rate limit")
        if response.status_code >= 400 or response.status_code in (301, 302, 303, 307, 308):
            raise KeitaroUnavailable("Keitaro rejected the request")
        try:
            payload: object = response.json()
        except ValueError as exc:
            raise KeitaroContractError("Keitaro returned invalid JSON") from exc
        return parse_streams(campaign_id, payload)
