"""Read-only FastAPI integration contract; all network requests are mocked."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from adrobot.config import Settings
from adrobot.main import create_app


def streams_fixture() -> list[dict[str, object]]:
    """Synthetic copy of the required live field shapes (never real credentials)."""
    return [
        {
            "id": 2002,
            "campaign_id": 501,
            "name": "Offers",
            "position": 2,
            "schema": "landings",
            "state": "active",
            "filters": [],
            "offers": [
                {
                    "id": 301,
                    "stream_id": 2002,
                    "offer_id": 42,
                    "state": "active",
                    "share": 100,
                    "private_token": "DO-NOT-EXPOSE",
                }
            ],
            "unknown_upstream_secret": "DO-NOT-EXPOSE",
        },
        {
            "id": 2001,
            "campaign_id": 501,
            "name": "Geo XY → Google",
            "position": 1,
            "schema": "redirect",
            "state": "active",
            "filters": [{"name": "country", "mode": "accept", "payload": ["XY"]}],
            "offers": [],
            "action_payload": "https://example.org/private-redirect",
        },
    ]


def test_health_and_pages_are_available_without_credentials(tmp_path: Path) -> None:
    database = tmp_path / "unused.db"
    app = create_app(
        Settings(
            keitaro_url=None,
            keitaro_api_key=None,
            database_url=f"sqlite:///{database}",
            _env_file=None,
        )
    )
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/editor").status_code == 200
        assert client.get("/").status_code == 200
        for path, content_type, marker in (
            ("/static/editor.js", "javascript", "fetch("),
            ("/static/styles.css", "text/css", "body"),
        ):
            response = client.get(path)
            assert response.status_code == 200
            assert content_type in response.headers["content-type"]
            assert marker in response.text
        assert client.get("/api/campaigns/123/editor").status_code == 503
    assert not database.exists()


def test_read_only_fetch_exposes_safe_ordered_streams_and_bindings() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=streams_fixture())

    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="synthetic-key",
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(handler),
    )
    with TestClient(app) as client:
        response = client.get("/api/campaigns/501/editor")
    assert response.status_code == 200
    assert response.json() == {
        "campaign_id": 501,
        "mapping_status": "remote_only",
        "read_only": True,
        "streams": [
            {
                "id": 2001,
                "name": "Geo XY → Google",
                "position": 1,
                "state": "active",
                "schema": "redirect",
                "kind": "non_offer",
                "offers": [],
            },
            {
                "id": 2002,
                "name": "Offers",
                "position": 2,
                "state": "active",
                "schema": "landings",
                "kind": "offers",
                "offers": [{"binding_id": 301, "offer_id": 42, "state": "active", "share": 100}],
            },
        ],
    }
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/admin_api/v1/campaigns/501/streams"
    assert "api_key" not in seen[0].url.params
    assert seen[0].headers["Api-Key"] == "synthetic-key"
    assert "DO-NOT-EXPOSE" not in response.text
    assert "private-redirect" not in response.text
    assert "synthetic-key" not in response.text


def test_empty_offer_stream_is_distinct_from_redirect() -> None:
    payload = streams_fixture()
    payload[0]["offers"] = []
    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="synthetic-key",
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    with TestClient(app) as client:
        response = client.get("/api/campaigns/501/editor")
    assert response.status_code == 200
    assert response.json()["streams"][1]["kind"] == "offers"
    assert response.json()["streams"][1]["offers"] == []


@pytest.mark.parametrize(
    "damage",
    [
        lambda _: {"streams": []},
        lambda _: [{"id": 2001, "name": "Missing required fields"}],
        lambda payload: [{**payload[0], "offers": None}],
        lambda payload: [{**payload[0], "offers": "wrong-type"}],
        lambda payload: [{**payload[0], "offers": [{"offer_id": 42}]}],
        lambda payload: [{**payload[0], "campaign_id": 900}],
        lambda payload: [{**payload[0], "offers": [{**payload[0]["offers"][0], "stream_id": 900}]}],
        lambda payload: [payload[0], payload[0]],
        lambda payload: [{**payload[0], "offers": [payload[0]["offers"][0]] * 2}],
        lambda payload: [{**payload[0], "offers": [{**payload[0]["offers"][0], "share": "100"}]}],
        lambda payload: [{**payload[0], "id": True}],
    ],
)
def test_malformed_or_partial_upstream_is_never_reported_as_success(
    damage: Callable[[list[dict[str, object]]], object],
) -> None:
    payload = damage(streams_fixture())
    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="synthetic-key",
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    with TestClient(app) as client:
        response = client.get("/api/campaigns/501/editor")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "keitaro_contract_error"
    assert response.json()["error"]["request_id"]
    assert "DO-NOT-EXPOSE" not in response.text


def test_invalid_json_is_contract_error() -> None:
    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="synthetic-key",
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(lambda _: httpx.Response(200, text="not JSON")),
    )
    with TestClient(app) as client:
        response = client.get("/api/campaigns/501/editor")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "keitaro_contract_error"


def test_keitaro_unauthorized_is_redacted() -> None:
    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="synthetic-key",
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(
            lambda _: httpx.Response(401, text="secret-from-upstream")
        ),
    )
    with TestClient(app) as client:
        response = client.get("/api/campaigns/501/editor")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "keitaro_unauthorized"
    assert "secret-from-upstream" not in response.text
    assert "synthetic-key" not in response.text


def test_timeout_does_not_turn_into_emptiness() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private connection details", request=request)

    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="synthetic-key",
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(handler),
    )
    with TestClient(app) as client:
        response = client.get("/api/campaigns/501/editor")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "keitaro_unavailable"
    assert "private connection details" not in response.text


@pytest.mark.parametrize(
    ("status", "expected_status", "expected_code"),
    [
        (404, 404, "keitaro_not_found"),
        (429, 503, "keitaro_rate_limited"),
        (502, 503, "keitaro_unavailable"),
        (302, 503, "keitaro_unavailable"),
    ],
)
def test_remote_http_failures_are_distinguished(
    status: int, expected_status: int, expected_code: str
) -> None:
    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="synthetic-key",
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(lambda _: httpx.Response(status)),
    )
    with TestClient(app) as client:
        response = client.get("/api/campaigns/501/editor")
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code


@pytest.mark.parametrize("existing_database", [False, True])
def test_remote_snapshot_matches_editor_without_touching_sqlite(
    tmp_path: Path, existing_database: bool
) -> None:
    database = tmp_path / "untouched.sqlite"
    if existing_database:
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
            connection.execute("INSERT INTO sentinel VALUES ('KEEP')")
    before = database.read_bytes() if existing_database else None
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=streams_fixture())

    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="synthetic-key",
            database_url=f"sqlite:///{database}",
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(handler),
    )
    with TestClient(app) as client:
        diagnostic = client.get("/api/campaigns/501/remote-snapshot")
        editor = client.get("/api/campaigns/501/editor")
    assert diagnostic.status_code == editor.status_code == 200
    assert diagnostic.json() == editor.json()
    assert diagnostic.json()["mapping_status"] == "remote_only"
    assert diagnostic.json()["read_only"] is True
    for response in (diagnostic, editor):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-request-id"]
        assert "DO-NOT-EXPOSE" not in response.text
        assert "private-redirect" not in response.text
    assert len(seen) == 2
    assert all(
        request.method == "GET" and request.url.path == "/admin_api/v1/campaigns/501/streams"
        for request in seen
    )
    if existing_database:
        assert database.read_bytes() == before
    else:
        assert not database.exists()


@pytest.mark.parametrize("campaign_id", ["0", "-1", "abc", "9007199254740992"])
def test_remote_snapshot_invalid_path_id_is_no_store_and_never_fetches(
    campaign_id: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=streams_fixture())

    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="synthetic-key",
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(handler),
    )
    with TestClient(app) as client:
        diagnostic = client.get(f"/api/campaigns/{campaign_id}/remote-snapshot")
        editor = client.get(f"/api/campaigns/{campaign_id}/editor")
    for response in (diagnostic, editor):
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-request-id"]
    assert requests == []


def test_remote_snapshot_no_store_is_limited_to_exact_path() -> None:
    app = create_app(Settings(keitaro_url=None, keitaro_api_key=None, _env_file=None))
    with TestClient(app) as client:
        failed = client.get("/api/campaigns/501/remote-snapshot")
        unrelated = client.get("/api/campaigns/501/not-a-route")
        extended = client.get("/api/campaigns/501/remote-snapshot/more")
        health = client.get("/health")
    assert failed.status_code == 503
    assert failed.headers["cache-control"] == "no-store"
    assert failed.headers["x-request-id"] == failed.json()["error"]["request_id"]
    for response in (unrelated, extended, health):
        assert "cache-control" not in response.headers
        assert response.headers["x-request-id"]


def test_invalid_campaign_id_does_not_call_upstream() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[])

    app = create_app(
        Settings(
            keitaro_url="https://tracker.example",
            keitaro_api_key="synthetic-key",
            _env_file=None,
        ),
        keitaro_transport=httpx.MockTransport(handler),
    )
    with TestClient(app) as client:
        response = client.get("/api/campaigns/0/editor")
    assert response.status_code == 422
    assert not calls
