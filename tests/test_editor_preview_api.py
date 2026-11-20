"""TASK-03/A: stateless Preview through the current FastAPI middleware."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from test_editor_domain import draft_payload

from adrobot.api import routes as preview_routes
from adrobot.config import Settings
from adrobot.keitaro.read_models import JS_SAFE_INTEGER
from adrobot.main import create_app

PREVIEW = "/api/campaigns/501/streams/2002/preview"


def offline_app(database: Path, transport: httpx.AsyncBaseTransport | None = None) -> Any:
    return create_app(
        Settings(
            keitaro_url="https://tracker.invalid" if transport else None,
            keitaro_api_key="fake-in-memory-key" if transport else None,
            database_url=f"sqlite:///{database}",
            _env_file=None,
        ),
        keitaro_transport=transport,
    )


def rows(response: httpx.Response) -> list[tuple[int, int, str, str]]:
    return [
        (row["id"], row["share"], row["status"], row["unit"]) for row in response.json()["rows"]
    ]


def assert_safe_error(response: httpx.Response, code: str, status: int = 422) -> None:
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "error": {
            "code": code,
            "message": response.json()["error"]["message"],
            "request_id": response.headers["X-Request-ID"],
        }
    }
    assert response.json()["error"]["message"]
    assert "Traceback" not in response.text


def test_preview_without_credentials_or_sqlite_does_not_create_database(tmp_path: Path) -> None:
    database = tmp_path / "should-not-be-created.db"
    with TestClient(offline_app(database)) as client:
        response = client.post(PREVIEW, json=draft_payload())
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-ID"]
    data = response.json()
    assert data["campaign_id"] == 501 and data["stream_id"] == 2002
    assert data["base_revision"] == "opaque-local-test-revision"
    assert data["base_pin_revision"] == "opaque-local-pin-revision"
    assert data["active_ids"] == [1, 2]
    assert data["pinned_shares"] == {}
    assert data["mode"] == "SNAPSHOT" and data["dirty"] is False
    assert data["share_unit"] == "kt_raw"
    assert rows(response) == [
        (1, 25, "Active", "kt_raw"),
        (2, 25, "Active", "kt_raw"),
        (3, 0, "Removed", "percentage_points"),
    ]
    assert not database.exists()


def test_preview_with_configured_keitaro_makes_zero_remote_requests(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def do_not_call(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("No Keitaro GET/POST/PUT/DELETE may occur on Preview")

    transport = httpx.MockTransport(do_not_call)
    database = tmp_path / "sentinel.sqlite"
    database.write_bytes(b"EXISTING-LOCAL-DATA-MUST-NOT-CHANGE")
    before = database.read_bytes()
    with TestClient(offline_app(database, transport)) as client:
        for item in (
            draft_payload(),
            draft_payload(active=[1, 2, 3], mode="RECALCULATED"),
            draft_payload(active=[1, 2, 3], mode="RECALCULATED", pins={1: 101}),
        ):
            response = client.post(PREVIEW, json=item)
            assert response.status_code in (200, 422)
    assert requests == []
    assert database.read_bytes() == before


def test_api_recalculation_and_pin_math(tmp_path: Path) -> None:
    with TestClient(offline_app(tmp_path / "unused.db")) as client:
        first = client.post(PREVIEW, json=draft_payload(active=[1, 2, 3], mode="RECALCULATED"))
        pinned = client.post(
            PREVIEW, json=draft_payload(active=[1, 2, 3], pins={1: 25}, mode="RECALCULATED")
        )
    assert first.status_code == pinned.status_code == 200
    assert rows(first) == [
        (1, 34, "Active", "percentage_points"),
        (2, 33, "Active", "percentage_points"),
        (3, 33, "Active", "percentage_points"),
    ]
    assert [entry[1] for entry in rows(pinned)] == [25, 38, 37]
    assert pinned.json()["pinned_shares"] == {"1": 25}


def test_api_verified_fixture_snapshot_pin_100_and_failed_recalc(tmp_path: Path) -> None:
    baseline = draft_payload(base=[(1, 100), (2, 25)])
    pin_a = {**baseline, "pinned_shares": {1: 100}}
    invalid_add = {**pin_a, "active_ids": [1, 2, 3], "mode": "RECALCULATED"}
    with TestClient(offline_app(tmp_path / "unused.db")) as client:
        first = client.post(PREVIEW, json=pin_a)
        invalid = client.post(PREVIEW, json=invalid_add)
        still_valid = client.post(PREVIEW, json=pin_a)
        unpin = client.post(PREVIEW, json=baseline)
    assert first.status_code == 200
    assert [r[1] for r in rows(first)] == [100, 25, 0]
    assert first.json()["mode"] == "SNAPSHOT" and first.json()["dirty"]
    assert_safe_error(invalid, "free_zero_with_unpinned")
    assert still_valid.json() == first.json()  # no server-side state to corrupt
    assert unpin.json()["mode"] == "SNAPSHOT" and not unpin.json()["dirty"]


def test_api_staged_add_remove_keeps_removed_row_while_clean(tmp_path: Path) -> None:
    base = draft_payload()
    staged = {
        **base,
        "draft_known_ids": [1, 2, 3, 4],
        "active_ids": [1, 2, 4],
        "mode": "RECALCULATED",
    }
    removed = {**staged, "active_ids": [1, 2], "pinned_shares": {}}
    with TestClient(offline_app(tmp_path / "unused.db")) as client:
        added = client.post(PREVIEW, json=staged)
        gone = client.post(PREVIEW, json=removed)
        restored = client.post(PREVIEW, json=staged)
        cancelled = client.post(PREVIEW, json=base)
    assert [
        added.status_code,
        gone.status_code,
        restored.status_code,
        cancelled.status_code,
    ] == [200] * 4
    assert [r[1] for r in rows(added) if r[2] == "Active"] == [34, 33, 33]
    assert gone.json()["mode"] == "SNAPSHOT" and gone.json()["dirty"] is False
    assert rows(gone)[-1] == (4, 0, "Removed", "percentage_points")
    assert rows(restored)[-1] == (4, 33, "Active", "percentage_points")
    assert len(rows(cancelled)) == 3


def test_api_empty_base_archived_unpin_works_without_live_open04(tmp_path: Path) -> None:
    base = draft_payload(base=[], known=[3], base_pins={3: 25}, active=[])
    unpin = {**base, "pinned_shares": {}}
    bringback = {**unpin, "active_ids": [3], "mode": "RECALCULATED"}
    with TestClient(offline_app(tmp_path / "unused.db")) as client:
        clean = client.post(PREVIEW, json=base)
        rejected = client.post(PREVIEW, json={**base, "active_ids": [3], "mode": "RECALCULATED"})
        dirty = client.post(PREVIEW, json=unpin)
        restored = client.post(PREVIEW, json=bringback)
    assert clean.status_code == 200 and clean.json()["mode"] == "SNAPSHOT"
    assert_safe_error(rejected, "incompatible_pin")
    assert dirty.status_code == 200 and dirty.json()["dirty"]
    assert rows(restored) == [(3, 100, "Active", "percentage_points")]


def test_preview_calls_are_independent_and_return_to_original_snapshot(tmp_path: Path) -> None:
    original = draft_payload()
    changed = {**original, "active_ids": [1, 2, 3], "mode": "RECALCULATED"}
    unchanged = deepcopy(original)
    with TestClient(offline_app(tmp_path / "unused.db")) as client:
        initial = client.post(PREVIEW, json=original)
        mid = client.post(PREVIEW, json=changed)
        final = client.post(PREVIEW, json={**unchanged, "mode": "RECALCULATED"})
    assert mid.json()["mode"] == "RECALCULATED" and mid.json()["dirty"]
    assert final.json() == initial.json()
    assert original == unchanged


@pytest.mark.parametrize(
    "damage",
    [
        lambda p: {**p, "base_offers": [{"id": 1, "raw_share": True}]},
        lambda p: {**p, "base_offers": [{"id": "1", "raw_share": 25}]},
        lambda p: {**p, "base_offers": [{"id": 1, "raw_share": 25}] * 2},
        lambda p: {**p, "base_known_ids": [1, 2, 2]},
        lambda p: {**p, "draft_known_ids": [3, 1, 2]},
        lambda p: {**p, "active_ids": [1, 2, 4]},
        lambda p: {**p, "pinned_shares": {"03": 25}},
        lambda p: {**p, "pinned_shares": {"3": 0}},
        lambda p: {**p, "pinned_shares": {"3": 25.0}},
        lambda p: {**p, "base_pinned_shares": {"4": 25}},
        lambda p: {**p, "base_known_ids": []},
        lambda p: {**p, "draft_known_ids": []},
        lambda p: {**p, "mode": "BROKEN"},
        lambda p: {**p, "private_token": "DO-NOT-EXPOSE"},
        lambda p: {key: value for key, value in p.items() if key != "pinned_shares"},
    ],
)
def test_api_rejects_bad_structures_with_safe_response(tmp_path: Path, damage: Any) -> None:
    with TestClient(offline_app(tmp_path / "unused.db")) as client:
        response = client.post(PREVIEW, json=damage(draft_payload()))
    assert_safe_error(response, "invalid_preview_input")
    assert "DO-NOT-EXPOSE" not in response.text


def test_preview_json_keys_are_strict_but_valid_decimal_keys_work(tmp_path: Path) -> None:
    valid = draft_payload(pins={1: 25})
    with TestClient(offline_app(tmp_path / "unused.db")) as client:
        good = client.post(PREVIEW, json=valid)
        raw = json.dumps(valid, separators=(",", ":"))
        duplicate = raw.replace('"pinned_shares":{"1":25}', '"pinned_shares":{"1":25,"1":25}')
        bad = client.post(PREVIEW, content=duplicate, headers={"content-type": "application/json"})
    assert good.status_code == 200
    assert good.json()["pinned_shares"] == {"1": 25}
    assert duplicate != raw  # ensure the duplicate key case was actually constructed
    assert_safe_error(bad, "invalid_preview_input")


@pytest.mark.parametrize(
    "content,content_type",
    [
        ("not-json", "application/json"),
        ("[]", "application/json"),
        ("null", "application/json"),
        ('{"data":NaN}', "application/json"),
        ('{"data":Infinity}', "application/json"),
        ("{}", "text/plain"),
        ("{}", "application/x-www-form-urlencoded"),
        (" " * (128 * 1024 + 1), "application/json"),
    ],
)
def test_invalid_or_oversize_body_is_rejected(
    tmp_path: Path, content: str, content_type: str
) -> None:
    with TestClient(offline_app(tmp_path / "unused.db")) as client:
        response = client.post(PREVIEW, content=content, headers={"content-type": content_type})
    assert_safe_error(response, "invalid_preview_input")


def test_cross_site_preview_rejected_before_body_or_remote_access(tmp_path: Path) -> None:
    with TestClient(offline_app(tmp_path / "unused.db")) as client:
        rejected = client.post(
            PREVIEW, json=draft_payload(), headers={"origin": "https://hostile.example"}
        )
        allowed = client.post(
            PREVIEW, json=draft_payload(), headers={"origin": "http://testserver"}
        )
    assert_safe_error(rejected, "invalid_origin", status=403)
    assert allowed.status_code == 200


@pytest.mark.parametrize(
    "campaign,stream",
    [
        ("0", "20"),
        ("20", "0"),
        (str(JS_SAFE_INTEGER + 1), "20"),
        ("20", str(JS_SAFE_INTEGER + 1)),
        ("-1", "20"),
        ("20", "abc"),
        ("01", "20"),
        ("20", "00"),
        ("20", "2.0"),
        ("１", "20"),
        ("20", "9" * 100),
    ],
)
def test_path_ids_use_safe_preview_error_envelope(
    tmp_path: Path, campaign: str, stream: str
) -> None:
    database = tmp_path / "must-not-be-created.db"
    with TestClient(offline_app(database)) as client:
        response = client.post(
            f"/api/campaigns/{campaign}/streams/{stream}/preview", json=draft_payload()
        )
    assert_safe_error(response, "invalid_preview_input")
    assert not database.exists()


def test_path_ids_accept_js_safe_upper_boundary(tmp_path: Path) -> None:
    with TestClient(offline_app(tmp_path / "unused.db")) as client:
        response = client.post(
            f"/api/campaigns/{JS_SAFE_INTEGER}/streams/{JS_SAFE_INTEGER}/preview",
            json=draft_payload(),
        )
    assert response.status_code == 200
    assert response.json()["campaign_id"] == JS_SAFE_INTEGER
    assert response.json()["stream_id"] == JS_SAFE_INTEGER
    assert response.headers["Cache-Control"] == "no-store"


def test_deeply_nested_json_returns_safe_error(tmp_path: Path) -> None:
    nested_json = "[" * 1500 + "0" + "]" * 1500
    with TestClient(offline_app(tmp_path / "unused.db")) as client:
        response = client.post(
            PREVIEW, content=nested_json, headers={"content-type": "application/json"}
        )
    assert_safe_error(response, "invalid_preview_input")


def test_json_decoder_recursion_error_returns_safe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deep_decode(_payload: bytes, **_options: object) -> object:
        raise RecursionError("untrusted-json-content-should-not-be-returned")

    with TestClient(offline_app(tmp_path / "unused.db")) as client:
        with monkeypatch.context() as scoped_patch:
            scoped_patch.setattr(
                preview_routes,
                "json",
                SimpleNamespace(loads=deep_decode, JSONDecodeError=json.JSONDecodeError),
            )
            response = client.post(
                PREVIEW, content="{}", headers={"content-type": "application/json"}
            )
    assert_safe_error(response, "invalid_preview_input")
    assert "untrusted-json-content" not in response.text


def test_empty_local_database_not_initialized_by_a_large_validation_error(tmp_path: Path) -> None:
    database = tmp_path / "never-initialized.db"
    huge = {**draft_payload(), "draft_known_ids": list(range(1, 2200))}
    with TestClient(offline_app(database)) as client:
        response = client.post(PREVIEW, json=huge)
    assert_safe_error(response, "invalid_preview_input")
    assert not database.exists()
