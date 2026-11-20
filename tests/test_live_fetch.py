"""Smoke helper exercises the actual FastAPI route without a network."""

import asyncio

import httpx
import pytest

from adrobot.config import Settings
from adrobot.keitaro.live_fetch import fetch_through_app


def settings() -> Settings:
    """Use a synthetic key; no test reads credentials or makes external requests."""
    return Settings(
        keitaro_url="https://tracker.example",
        keitaro_api_key="synthetic-key",
        _env_file=None,
    )


def test_readonly_smoke_exercises_route_and_counts() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "id": 51,
                    "campaign_id": 20,
                    "name": "Offers",
                    "position": 1,
                    "state": "active",
                    "schema": "landings",
                    "filters": [],
                    "offers": [
                        {
                            "id": 80,
                            "stream_id": 51,
                            "offer_id": 99,
                            "state": "active",
                            "share": 100,
                            "credential": "PRIVATE-DATA",
                        }
                    ],
                }
            ],
        )

    result = asyncio.run(
        fetch_through_app(20, settings(), keitaro_transport=httpx.MockTransport(handler))
    )
    assert result == {
        "status": "ok",
        "campaign_id": 20,
        "streams": 1,
        "offer_streams": 1,
        "bindings": 1,
    }
    assert len(requests) == 1
    assert requests[0].url.path == "/admin_api/v1/campaigns/20/streams"
    assert requests[0].headers["Api-Key"] == "synthetic-key"
    assert "PRIVATE-DATA" not in str(result)


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "keitaro_unauthorized"), (422, "keitaro_upstream_validation_error")],
)
def test_readonly_smoke_reports_safe_upstream_failure(status: int, code: str) -> None:
    with pytest.raises(RuntimeError, match=f"HTTP 502, {code}") as exc:
        asyncio.run(
            fetch_through_app(
                20,
                settings(),
                keitaro_transport=httpx.MockTransport(
                    lambda _: httpx.Response(status, text="PRIVATE-AUTH-INFO")
                ),
            )
        )
    assert "PRIVATE-AUTH-INFO" not in str(exc.value)
