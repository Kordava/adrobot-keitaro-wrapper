"""Read-only Keitaro Admin API transport with strict response contracts."""

from typing import NoReturn

import httpx

from adrobot.keitaro.exceptions import (
    KeitaroContractError,
    KeitaroNotFound,
    KeitaroRateLimited,
    KeitaroUnauthorized,
    KeitaroUnavailable,
    KeitaroUpstreamValidationError,
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
