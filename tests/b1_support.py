"""Offline Keitaro fixtures and real Alembic helpers for TASK-03 B1 tests."""

import os
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from adrobot.config import Settings
from adrobot.main import create_app

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = 501


def sqlite_url(path: Path) -> str:
    return "sqlite:///" + str(path)


def migrate(path: Path, database_url: str | None = None) -> None:
    env = dict(os.environ)
    env["ADROBOT_DATABASE_URL"] = database_url or sqlite_url(path)
    # The test DB and directory are always isolated. Never use the project DB.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    if result.returncode:
        raise AssertionError(f"Alembic failed: {result.stderr[:1500]}")


def stream(
    sid: int = 11,
    pairs: list[tuple[int, int, int, str]] | None = None,
    *,
    campaign_id: int = CAMPAIGN,
    position: int = 1,
    schema: str = "landings",
) -> dict[str, Any]:
    """A binding tuple is (offer ID, binding ID, raw share, Keitaro state)."""
    if pairs is None:
        pairs = [(101, 201, 25, "active"), (102, 202, 25, "active")]
    return {
        "id": sid,
        "campaign_id": campaign_id,
        "name": f"Stream {sid}",
        "position": position,
        "state": "active",
        "schema": schema,
        "filters": [],
        "offers": [
            {"id": binding, "stream_id": sid, "offer_id": offer, "share": share, "state": state}
            for offer, binding, share, state in pairs
        ],
        "private_secret": "NEVER-RETURN-THIS",
    }


class FakeKeitaro:
    def __init__(self, streams: list[dict[str, Any]] | None = None) -> None:
        self.streams = streams if streams is not None else [stream()]
        self.names: dict[int, str] = {101: "Name v1", 102: "Anchor B", 103: "Third"}
        self.offer_states: dict[int, str] = {}
        self.catalog_ids: set[int] | None = None
        self.calls: list[str] = []
        self.partial_catalog = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET", "B1 must not write to Keitaro"
        path = request.url.path
        self.calls.append(path)
        if path.endswith("/streams"):
            return httpx.Response(200, json=deepcopy(self.streams))
        if path.endswith("/offers"):
            ids = sorted(self.names if self.catalog_ids is None else self.catalog_ids)
            return httpx.Response(
                200,
                json=[
                    {
                        "id": oid,
                        "name": self.names[oid],
                        "state": self.offer_states.get(oid, "active"),
                    }
                    for oid in ids
                ],
                headers={"Content-Range": "0-1/4"} if self.partial_catalog else {},
            )
        if "/offers/" in path:
            oid = int(path.rsplit("/", 1)[-1])
            if oid not in self.names:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "id": oid,
                    "name": self.names[oid],
                    "state": self.offer_states.get(oid, "active"),
                },
            )
        raise AssertionError(f"Unexpected upstream path {path}")


def app_for(db: Path, fake: FakeKeitaro, *, database_url: str | None = None):
    return create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="SAFE-TEST-SECRET",
            database_url=database_url or sqlite_url(db),
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(fake.handler),
    )


def editor(client: TestClient, campaign_id: int = CAMPAIGN) -> httpx.Response:
    return client.get(f"/api/campaigns/{campaign_id}/editor")


def pin(db: Path, sid: int, oid: int, share: int | None) -> None:
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE known_offer_bindings SET pinned_share=? WHERE "
            "campaign_id=? AND stream_id=? AND offer_id=?",
            (share, CAMPAIGN, sid, oid),
        )


def db_rows(db: Path) -> list[tuple[Any, ...]]:
    with sqlite3.connect(db) as connection:
        return connection.execute(
            "SELECT campaign_id, stream_id, offer_id, last_binding_id, offer_name, "
            "pinned_share, last_seen_share, last_seen_kt_state, first_seen_at, "
            "last_seen_at, removed_at FROM known_offer_bindings ORDER BY stream_id,offer_id"
        ).fetchall()
