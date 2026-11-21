"""Stage A Preview capacity is an atomic pre-DML gate for the entire campaign."""

import sqlite3
from pathlib import Path

from b1_support import FakeKeitaro, app_for, db_rows, editor, migrate, stream
from fastapi.testclient import TestClient
from sqlalchemy import event


def _many_stream(sid: int, ids: range | list[int]) -> dict:
    return stream(sid, [(oid, oid + 2000000, 25, "active") for oid in ids])


def _names(fake: FakeKeitaro, *ranges: range | list[int]) -> None:
    for ids in ranges:
        fake.names.update({oid: f"Offer {oid}" for oid in ids})


def _spy(engine) -> list[str]:
    sql: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ")):
            sql.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    return sql


def test_2048_short_ids_fit_but_2049_fail_without_archive_writes(tmp_path: Path) -> None:
    db = tmp_path / "max_items.db"
    migrate(db)
    ids = range(1, 2049)
    fake = FakeKeitaro([_many_stream(11, ids)])
    _names(fake, ids, [2049])
    app = app_for(db, fake)
    with TestClient(app) as client:
        accepted = editor(client)
        assert accepted.status_code == 200, accepted.text[:500]
        base = accepted.json()["streams"][0]["editor"]
        assert len(base["base_known_ids"]) == 2048
        preview = client.post("/api/campaigns/501/streams/11/preview", json=base)
        assert preview.status_code == 200, preview.text[:500]
        assert preview.json()["dirty"] is False
        assert len(preview.json()["rows"]) == 2048
        before = db_rows(db)
        sql = _spy(app.state.editor_coordinator.engine)
        fake.streams = [_many_stream(11, range(1, 2050))]
        rejected = editor(client)
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "editor_snapshot_too_large"
        assert sql == []
        assert db_rows(db) == before


def test_1100_long_ids_and_full_pins_exceed_128k_body_without_dml(tmp_path: Path) -> None:
    db = tmp_path / "max_body.db"
    migrate(db)
    ids = list(range(10**15, 10**15 + 1100))
    fake = FakeKeitaro([_many_stream(11, ids)])
    _names(fake, ids)
    app = app_for(db, fake)
    with TestClient(app) as client:
        accepted = editor(client)
        assert accepted.status_code == 200, accepted.text[:400]
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE known_offer_bindings SET pinned_share=1")
        before = db_rows(db)
        trace = _spy(app.state.editor_coordinator.engine)
        rejected = editor(client)
        assert rejected.status_code == 409, rejected.text[:400]
        assert rejected.json()["error"]["code"] == "editor_snapshot_too_large"
        assert trace == []
        assert db_rows(db) == before
        # The diagnostic B0 endpoint is unaffected by local Preview limits.
        assert client.get("/api/campaigns/501/remote-snapshot").status_code == 200


def test_second_stream_overflow_aborts_first_stream_before_dml(tmp_path: Path) -> None:
    db = tmp_path / "two_stream_capacity.db"
    migrate(db)
    first_ids = [101]
    big_ids = list(range(10000, 12049))
    fake = FakeKeitaro([_many_stream(11, first_ids), _many_stream(12, [102])])
    _names(fake, first_ids, big_ids, [102])
    app = app_for(db, fake)
    with TestClient(app) as client:
        assert editor(client).status_code == 200
        before = db_rows(db)
        sql = _spy(app.state.editor_coordinator.engine)
        fake.streams = [_many_stream(11, first_ids), _many_stream(12, big_ids)]
        response = editor(client)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "editor_snapshot_too_large"
        assert db_rows(db) == before
        assert sql == []


def test_old_archive_plus_new_remote_rows_exceeds_capacity(tmp_path: Path) -> None:
    db = tmp_path / "historical_capacity.db"
    migrate(db)
    old_ids = list(range(1000, 2800))
    new_ids = list(range(3000, 3300))
    fake = FakeKeitaro([_many_stream(11, old_ids)])
    _names(fake, old_ids, new_ids)
    app = app_for(db, fake)
    with TestClient(app) as client:
        assert editor(client).status_code == 200
        before = db_rows(db)
        sql = _spy(app.state.editor_coordinator.engine)
        # Existing 1800 historical rows + 300 new IDs => 2100 known.
        fake.streams = [_many_stream(11, [old_ids[0], *new_ids])]
        result = editor(client)
        assert result.status_code == 409
        assert result.json()["error"]["code"] == "editor_snapshot_too_large"
        assert db_rows(db) == before
        assert sql == []
