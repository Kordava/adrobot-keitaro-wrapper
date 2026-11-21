"""Editor Fetch through HTTP, campaign-wide atomicity and diagnostic isolation."""

import sqlite3
from pathlib import Path

import httpx
import pytest
from b1_support import (
    FakeKeitaro,
    app_for,
    db_rows,
    editor,
    migrate,
    pin,
    stream,
)
from fastapi.testclient import TestClient
from sqlalchemy import event

PREVIEW = "/api/campaigns/501/streams/11/preview"
STREAMS_PATH = "/admin_api/v1/campaigns/501/streams"


def _dml_spy(app) -> list[str]:
    statements: list[str] = []
    engine = app.state.editor_coordinator.engine
    assert engine is not None

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    return statements


def _check_error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json() == {
        "error": {
            "code": code,
            "message": response.json()["error"]["message"],
            "request_id": response.headers["x-request-id"],
        }
    }
    assert response.headers["cache-control"] == "no-store"


def test_duplicate_in_second_stream_rejects_whole_campaign_with_zero_dml(tmp_path: Path) -> None:
    db = tmp_path / "duplicate.db"
    migrate(db)
    fake = FakeKeitaro(
        [
            stream(11, [(101, 201, 25, "active"), (102, 202, 25, "active")]),
            stream(12, [(103, 203, 100, "active")], position=2),
        ]
    )
    app = app_for(db, fake)
    with TestClient(app) as client:
        first = editor(client)
        assert first.status_code == 200
        pin(db, 11, 101, 25)
        pin(db, 12, 103, 40)
        before = db_rows(db)
        trace = _dml_spy(app)
        # Duplicate logical ID but DISTINCT binding IDs. B0 must show both.
        fake.streams[0]["offers"][0]["share"] = 65
        fake.streams[1]["offers"].append(
            {
                "id": 204,
                "stream_id": 12,
                "offer_id": 103,
                "state": "disabled",
                "share": 0,
            }
        )
        rejected = editor(client)
        _check_error(rejected, 409, "editor_duplicate_offer_id")
        assert trace == [], "No INSERT/UPDATE/DELETE, even transient before rollback"
        assert db_rows(db) == before
        diagnostic = client.get("/api/campaigns/501/remote-snapshot")
        assert diagnostic.status_code == 200
        assert diagnostic.json()["mapping_status"] == "remote_only"
        offers = diagnostic.json()["streams"][1]["offers"]
        assert [r["binding_id"] for r in offers] == [203, 204]
        assert [r["offer_id"] for r in offers] == [103, 103]
        assert len(fake.calls) > 0
        assert "NEVER-RETURN-THIS" not in rejected.text + diagnostic.text
        # The previously displayed editor snapshot is still an independently
        # valid base. A retry cannot destructively replace it on failure.
        assert first.json()["streams"][0]["editor"]["base_known_ids"] == [101, 102]


@pytest.mark.parametrize(
    ("mutate", "code", "archived"),
    [
        (
            lambda items: items[0]["offers"][0].update(state="unknown"),
            "editor_binding_state_unrecognized",
            False,
        ),
        (lambda items: items.pop(), "editor_known_stream_missing", False),
        (lambda items: items.pop(), "editor_known_stream_missing", True),
        (
            lambda items: items[1].update(schema="redirect", offers=[]),
            "editor_known_stream_type_changed",
            False,
        ),
        (
            lambda items: items[1].update(schema="redirect", offers=[]),
            "editor_known_stream_type_changed",
            True,
        ),
    ],
)
def test_bad_or_missing_stream_never_writes(
    tmp_path: Path, mutate, code: str, archived: bool
) -> None:
    db = tmp_path / "unchanged.db"
    migrate(db)
    fake = FakeKeitaro(
        [
            stream(11, [(101, 201, 50, "active")]),
            stream(12, [(102, 202, 100, "active")], position=2),
        ]
    )
    app = app_for(db, fake)
    with TestClient(app) as client:
        assert editor(client).status_code == 200
        if archived:
            fake.streams[1]["offers"].clear()
            archived_response = editor(client)
            assert archived_response.status_code == 200, archived_response.text
            assert archived_response.json()["streams"][1]["known_offers"][0]["status"] == "Removed"
        before = db_rows(db)
        spy = _dml_spy(app)
        mutate(fake.streams)
        _check_error(editor(client), 409, code)
        assert db_rows(db) == before
        assert spy == []


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda item: item.pop("offers"), id="absent"),
        pytest.param(lambda item: item.update(offers=None), id="null"),
        pytest.param(lambda item: item.update(offers={}), id="object"),
    ],
)
def test_empty_stream_with_malformed_neighbor_rejects_entire_fetch(tmp_path: Path, damage) -> None:
    db = tmp_path / "malformed-neighbor.db"
    migrate(db)
    fake = FakeKeitaro([stream(), stream(12, [(103, 203, 100, "active")], position=2)])
    app = app_for(db, fake)
    with TestClient(app) as client:
        seeded = editor(client)
        assert seeded.status_code == 200, seeded.text
        pin(db, 11, 101, 25)
        pin(db, 12, 103, 50)
        before = db_rows(db)
        spy = _dml_spy(app)

        fake.streams[0]["offers"] = []
        damage(fake.streams[1])
        _check_error(editor(client), 502, "keitaro_contract_error")
        assert spy == [], "No INSERT/UPDATE/DELETE, even before a rollback"
        assert db_rows(db) == before


def test_empty_stream_archives_and_restores_bindings_through_fetch(tmp_path: Path) -> None:
    db = tmp_path / "empty-lifecycle.db"
    migrate(db)
    neighbor = stream(12, [(103, 203, 100, "active")], position=2)
    fake = FakeKeitaro(
        [stream(pairs=[(101, 201, 25, "disabled"), (102, 202, 75, "active")]), neighbor]
    )
    with TestClient(app_for(db, fake)) as client:
        assert editor(client).status_code == 200
        pin(db, 11, 101, 25)
        pin(db, 11, 102, 75)
        pin(db, 12, 103, 40)
        fake.streams[0]["offers"].pop()
        partial = editor(client)
        assert partial.status_code == 200, partial.text
        previous, previous_neighbor = partial.json()["streams"]
        before = db_rows(db)
        assert before[0][-1] is None
        assert before[1][-1] is not None

        fake.streams[0]["offers"] = []
        result = editor(client)
        assert result.status_code == 200, result.text
        snapshot = result.json()
        assert snapshot["mapping_status"] == "archive_synced"
        assert snapshot["local_archive_synced"] is True
        empty, current_neighbor = snapshot["streams"]
        assert empty["kind"] == "offers"
        assert empty["offers"] == []
        assert empty["editor"]["base_offers"] == empty["editor"]["active_ids"] == []
        assert empty["editor"]["base_known_ids"] == empty["editor"]["draft_known_ids"] == [101, 102]
        assert (
            empty["editor"]["base_pinned_shares"]
            == empty["editor"]["pinned_shares"]
            == {
                "101": 25,
                "102": 75,
            }
        )
        first_removed, already_removed = empty["known_offers"]
        assert first_removed["removed_at"] is not None
        assert first_removed == {
            **previous["known_offers"][0],
            "status": "Removed",
            "binding_id": None,
            "kt_state": None,
            "display_share": 0,
            "display_share_unit": "percentage_points",
            "removed_at": first_removed["removed_at"],
        }
        assert already_removed == previous["known_offers"][1]
        assert current_neighbor["offers"] == previous_neighbor["offers"]
        assert current_neighbor["editor"] == previous_neighbor["editor"]
        assert current_neighbor["known_offers"][0]["status"] == "Active"
        after = db_rows(db)
        assert after[0][:-1] == before[0][:-1] and after[0][-1] is not None
        assert after[1] == before[1]
        assert after[2][:9] == before[2][:9] and after[2][-1] is None

        repeat = editor(client)
        assert repeat.status_code == 200, repeat.text
        assert repeat.json()["streams"][0] == empty
        assert db_rows(db)[:2] == after[:2]

        fake.names[101] = "Renamed after return"
        fake.streams[0] = stream(pairs=[(101, 301, 100, "active")])
        restored = editor(client)
        assert restored.status_code == 200, restored.text
        restored_row, remaining_archive = restored.json()["streams"][0]["known_offers"]
        assert restored_row == {
            **first_removed,
            "binding_id": 301,
            "last_binding_id": 301,
            "name": "Renamed after return",
            "status": "Active",
            "kt_state": "active",
            "last_seen_kt_state": "active",
            "display_share": 100,
            "display_share_unit": "kt_raw",
            "last_seen_raw_share": 100,
            "last_seen_at": restored_row["last_seen_at"],
            "removed_at": None,
        }
        assert restored_row["last_seen_at"] > first_removed["last_seen_at"]
        assert remaining_archive == already_removed
        rows = db_rows(db)
        assert len(rows) == 3
        assert rows[0][3:8] == (301, "Renamed after return", 25, 100, "active")
        assert rows[0][8] == before[0][8] and rows[0][-1] is None
        assert rows[1] == before[1]


@pytest.mark.parametrize("replacement", [False, True], ids=["absent", "redirect"])
def test_first_seen_empty_stream_has_preview_without_archive(
    tmp_path: Path, replacement: bool
) -> None:
    db = tmp_path / "first-empty.db"
    migrate(db)
    redirect = stream(12, [], position=2, schema="redirect")
    fake = FakeKeitaro([stream(pairs=[]), redirect])
    app = app_for(db, fake)
    with TestClient(app) as client:
        result = editor(client)
        assert result.status_code == 200, result.text
        empty, non_offer = result.json()["streams"]
        assert empty["kind"] == "offers"
        assert empty["offers"] == empty["known_offers"] == []
        assert empty["editor"]["base_known_ids"] == empty["editor"]["draft_known_ids"] == []
        assert empty["editor"]["base_offers"] == empty["editor"]["active_ids"] == []
        assert empty["editor"]["base_pinned_shares"] == empty["editor"]["pinned_shares"] == {}
        assert non_offer["kind"] == "non_offer"
        assert non_offer["offers"] == non_offer["known_offers"] == []
        assert non_offer["editor"] is None
        preview = client.post(PREVIEW, json=empty["editor"])
        assert preview.status_code == 200, preview.text
        assert preview.json()["mode"] == "SNAPSHOT"
        assert preview.json()["dirty"] is False
        assert preview.json()["rows"] == []
        diagnostic = client.get("/api/campaigns/501/remote-snapshot")
        assert diagnostic.status_code == 200
        assert diagnostic.json()["mapping_status"] == "remote_only"
        assert [(s["kind"], s["offers"]) for s in diagnostic.json()["streams"]] == [
            ("offers", []),
            ("non_offer", []),
        ]
        assert db_rows(db) == []

        spy = _dml_spy(app)
        fake.streams = (
            [stream(pairs=[], schema="redirect"), redirect] if replacement else [redirect]
        )
        followup = editor(client)
        assert followup.status_code == 200, followup.text
        streams = followup.json()["streams"]
        assert [s["id"] for s in streams] == ([11, 12] if replacement else [12])
        assert all(s["kind"] == "non_offer" and s["editor"] is None for s in streams)
        assert fake.calls == [STREAMS_PATH] * 3
        assert db_rows(db) == []
        assert spy == []


def test_preview_from_fetched_empty_base_preserves_pin_rules_and_archive(tmp_path: Path) -> None:
    db = tmp_path / "empty-preview.db"
    migrate(db)
    fake = FakeKeitaro([stream(pairs=[(101, 201, 25, "active")])])
    app = app_for(db, fake)
    with TestClient(app) as client:
        assert editor(client).status_code == 200
        pin(db, 11, 101, 25)
        initial = editor(client)
        assert initial.status_code == 200, initial.text
        active_base = initial.json()["streams"][0]["editor"]
        fake.streams[0]["offers"] = []
        fetched = editor(client)
        assert fetched.status_code == 200, fetched.text
        empty_base = fetched.json()["streams"][0]["editor"]
        before = db_rows(db)
        spy = _dml_spy(app)
        fake.calls.clear()

        clean = client.post(PREVIEW, json=empty_base)
        assert clean.status_code == 200, clean.text
        assert clean.json()["mode"] == "SNAPSHOT"
        assert clean.json()["dirty"] is False
        assert clean.json()["pinned_shares"] == {"101": 25}
        assert clean.json()["rows"] == [
            {
                "id": 101,
                "share": 0,
                "status": "Removed",
                "unit": "percentage_points",
                "pinned_share": 25,
            }
        ]

        bring_back = {**empty_base, "active_ids": [101], "mode": "RECALCULATED"}
        _check_error(client.post(PREVIEW, json=bring_back), 422, "incompatible_pin")
        accepted = client.post(PREVIEW, json={**bring_back, "pinned_shares": {}})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["mode"] == "RECALCULATED"
        assert accepted.json()["dirty"] is True
        assert accepted.json()["pinned_shares"] == {}
        assert accepted.json()["rows"] == [
            {
                "id": 101,
                "share": 100,
                "status": "Active",
                "unit": "percentage_points",
                "pinned_share": None,
            }
        ]

        remove_last = {**active_base, "active_ids": [], "mode": "RECALCULATED"}
        _check_error(client.post(PREVIEW, json=remove_last), 422, "empty_active_forbidden")
        assert fake.calls == []
        assert spy == []
        assert db_rows(db) == before


def test_full_remote_read_failure_does_not_mark_removed(tmp_path: Path) -> None:
    db = tmp_path / "readfail.db"
    migrate(db)
    fake = FakeKeitaro([stream(), stream(12, [(103, 203, 100, "active")], position=2)])
    app = app_for(db, fake)
    with TestClient(app) as client:
        assert editor(client).status_code == 200
        before = db_rows(db)
        spy = _dml_spy(app)
        fake.partial_catalog = True
        # A partial offer listing is a typed integration failure before SQL.
        res = editor(client)
        assert res.status_code == 502
        assert res.json()["error"]["code"] == "keitaro_contract_error"
        assert spy == []
        assert db_rows(db) == before


def test_missing_name_direct_get_allows_disabled_global_offer(tmp_path: Path) -> None:
    db = tmp_path / "offer_names.db"
    migrate(db)
    fake = FakeKeitaro([stream(pairs=[(101, 201, 100, "disabled")])])
    fake.catalog_ids = set()  # Inactive offer absent from the search/list view.
    fake.offer_states[101] = "disabled"
    app = app_for(db, fake)
    with TestClient(app) as client:
        res = editor(client)
        assert res.status_code == 200
        assert res.json()["streams"][0]["known_offers"][0]["status"] == "Active"
        assert res.json()["streams"][0]["known_offers"][0]["name"] == "Name v1"
        assert "/admin_api/v1/offers/101" in fake.calls
        before = db_rows(db)
        spy = _dml_spy(app)
        fake.names.pop(101)
        rejected = editor(client)
        assert rejected.status_code == 404
        assert spy == []
        assert db_rows(db) == before


def test_missing_runtime_db_and_unmigrated_db_do_not_create_any_tables(tmp_path: Path) -> None:
    db = tmp_path / "missing.sqlite"
    fake = FakeKeitaro()
    with TestClient(app_for(db, fake)) as client:
        response = editor(client)
        _check_error(response, 503, "local_archive_unavailable")
        assert client.get("/health").status_code == 200
        assert client.get("/api/campaigns/501/remote-snapshot").status_code == 200
    assert not db.exists()
    assert not list(tmp_path.glob("*.sqlite-*"))

    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE sentinel (value TEXT)")
        conn.execute("INSERT INTO sentinel VALUES ('KEEP')")
    with TestClient(app_for(db, fake)) as client:
        response = editor(client)
        _check_error(response, 503, "local_archive_schema_missing")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT value FROM sentinel").fetchone() == ("KEEP",)
        tables = [
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        assert tables == ["sentinel"]


def test_failed_fetch_does_not_modify_existing_archive_with_unrelated_data(tmp_path: Path) -> None:
    db = tmp_path / "failure.sqlite"
    migrate(db)
    fake = FakeKeitaro()
    app = app_for(db, fake)
    with TestClient(app) as client:
        first = editor(client)
        assert first.status_code == 200
        before = db_rows(db)
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE sentinel (payload TEXT)")
            conn.execute("INSERT INTO sentinel VALUES ('untouched')")
        fake.streams[0]["offers"][0]["state"] = "paused"
        spy = _dml_spy(app)
        response = editor(client)
        _check_error(response, 409, "editor_binding_state_unrecognized")
        assert spy == []
        assert db_rows(db) == before
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT payload FROM sentinel").fetchone() == ("untouched",)


def test_duplicate_binding_id_still_fails_shared_keitaro_parser(tmp_path: Path) -> None:
    db = tmp_path / "binding_collision.db"
    migrate(db)
    fake = FakeKeitaro([stream(pairs=[(101, 201, 50, "active"), (102, 201, 50, "disabled")])])
    with TestClient(app_for(db, fake)) as client:
        res = editor(client)
        assert res.status_code == 502
        assert res.json()["error"]["code"] == "keitaro_contract_error"
        assert db_rows(db) == []


def test_editor_response_does_not_leak_private_upstream_fields(tmp_path: Path) -> None:
    db = tmp_path / "private.db"
    migrate(db)
    fake = FakeKeitaro()
    with TestClient(app_for(db, fake)) as client:
        response = editor(client)
        assert response.status_code == 200
        assert "NEVER-RETURN-THIS" not in response.text
        assert "SAFE-TEST-SECRET" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["keitaro_write_enabled"] is False
        assert response.json()["local_archive_synced"] is True
