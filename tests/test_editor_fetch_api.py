"""B1: fail-closed editor Fetch, two-stream atomicity and unaffected diagnostic B0."""

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
    ("mutate", "status", "code"),
    [
        (lambda items: items[0]["offers"].clear(), 409, "editor_empty_offer_stream_unconfirmed"),
        (
            lambda items: items[0]["offers"][0].update(state="unknown"),
            409,
            "editor_binding_state_unrecognized",
        ),
        (lambda items: items.pop(), 409, "editor_known_stream_missing"),
        (
            lambda items: items[1].update(schema="redirect", offers=[]),
            409,
            "editor_known_stream_type_changed",
        ),
    ],
)
def test_bad_or_missing_stream_never_writes(tmp_path: Path, mutate, status: int, code: str) -> None:
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
        before = db_rows(db)
        spy = _dml_spy(app)
        mutate(fake.streams)
        res = editor(client)
        _check_error(res, status, code)
        assert db_rows(db) == before
        assert spy == []
        if code == "editor_empty_offer_stream_unconfirmed":
            diagnostic = client.get("/api/campaigns/501/remote-snapshot")
            assert diagnostic.status_code == 200
            assert diagnostic.json()["streams"][0]["offers"] == []


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
