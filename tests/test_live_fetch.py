"""Smoke helper exercises the actual FastAPI route without a network."""

import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest
from b1_support import stream
from fastapi import FastAPI

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


@pytest.mark.parametrize("stream_count", [1, 2])
def test_readonly_smoke_counts_duplicate_offers_as_distinct_bindings(stream_count: int) -> None:
    streams = [
        stream(
            sid=51 + i,
            pairs=[(99, 80 + 2 * i, 25, "active"), (99, 81 + 2 * i, 25, "disabled")],
            campaign_id=20,
            position=i + 1,
        )
        for i in range(stream_count)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/admin_api/v1/campaigns/20/streams"
        return httpx.Response(200, json=streams)

    assert asyncio.run(
        fetch_through_app(20, settings(), keitaro_transport=httpx.MockTransport(handler))
    ) == {
        "status": "ok",
        "campaign_id": 20,
        "streams": stream_count,
        "offer_streams": stream_count,
        "bindings": 2 * stream_count,
    }


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


def test_smoke_calls_only_the_diagnostic_app_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI must not follow a future archive-synced /editor route."""
    app = FastAPI()
    called: list[int] = []

    @app.get("/api/campaigns/{campaign_id}/remote-snapshot")
    async def only_diagnostic_route(campaign_id: int) -> dict[str, object]:
        called.append(campaign_id)
        return {
            "campaign_id": campaign_id,
            "mapping_status": "remote_only",
            "read_only": True,
            "streams": [],
        }

    monkeypatch.setattr("adrobot.main.create_app", lambda *_args, **_kwargs: app)
    result = asyncio.run(fetch_through_app(20, settings()))
    assert called == [20]
    assert result == {
        "status": "ok",
        "campaign_id": 20,
        "streams": 0,
        "offer_streams": 0,
        "bindings": 0,
    }


@pytest.mark.parametrize(
    "markers",
    [
        {"read_only": False, "mapping_status": "remote_only"},
        {"read_only": True, "mapping_status": "archive_synced"},
        {"mapping_status": "remote_only"},
        {"read_only": True},
    ],
)
def test_smoke_rejects_wrong_contract(
    monkeypatch: pytest.MonkeyPatch, markers: dict[str, object]
) -> None:
    app = FastAPI()

    @app.get("/api/campaigns/{campaign_id}/remote-snapshot")
    async def wrong_contract(campaign_id: int) -> dict[str, object]:
        return {"campaign_id": campaign_id, "streams": [], **markers}

    monkeypatch.setattr("adrobot.main.create_app", lambda *_args, **_kwargs: app)
    with pytest.raises(RuntimeError, match="response contract mismatch"):
        asyncio.run(fetch_through_app(20, settings()))


@pytest.mark.parametrize("existing_database", [False, True])
@pytest.mark.parametrize("upstream_status", [200, 503])
def test_smoke_never_reads_or_writes_local_database(
    tmp_path: Path, existing_database: bool, upstream_status: int
) -> None:
    database = tmp_path / "untouched.sqlite"
    if existing_database:
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
            connection.execute("INSERT INTO sentinel VALUES ('KEEP')")
    original = database.read_bytes() if existing_database else None
    config = Settings(
        keitaro_url="https://tracker.example",
        keitaro_api_key="synthetic-key",
        database_url=f"sqlite:///{database}",
        _env_file=None,
    )
    transport = httpx.MockTransport(
        lambda _: httpx.Response(upstream_status, json=[] if upstream_status == 200 else None)
    )
    if upstream_status == 200:
        result = asyncio.run(fetch_through_app(20, config, keitaro_transport=transport))
        assert result["status"] == "ok"
    else:
        with pytest.raises(RuntimeError, match="Fetch failed: HTTP 503"):
            asyncio.run(fetch_through_app(20, config, keitaro_transport=transport))
    if existing_database:
        assert database.read_bytes() == original
    else:
        assert not database.exists()
