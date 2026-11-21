"""B1 logical identity, persistence, pin canonicalization and per-stream snapshots."""

import hashlib
import json
from pathlib import Path

import pytest
from b1_support import (
    CAMPAIGN,
    FakeKeitaro,
    app_for,
    db_rows,
    editor,
    migrate,
    pin,
    stream,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from adrobot.db.archive import pin_revision
from adrobot.db.models import KnownOfferBinding
from adrobot.editor.schemas import PreviewInput


def test_binding_id_churn_preserves_one_row_pin_first_seen_and_revision(tmp_path: Path) -> None:
    db = tmp_path / "offer_archive.db"
    migrate(db)
    fake = FakeKeitaro([stream(pairs=[(101, 2092367, 25, "active"), (102, 220, 25, "active")])])
    with TestClient(app_for(db, fake)) as client:
        t0 = editor(client)
        assert t0.status_code == 200, t0.text
        pin(db, 11, 101, 25)
        t0 = editor(client).json()["streams"][0]
        first = db_rows(db)[0]
        assert t0["editor"]["base_known_ids"] == [101, 102]
        assert t0["editor"]["base_offers"] == [
            {"id": 101, "raw_share": 25},
            {"id": 102, "raw_share": 25},
        ]
        assert t0["editor"]["base_pinned_shares"] == {"101": 25}
        assert t0["known_offers"][0]["name"] == "Name v1"

        fake.streams = [stream(pairs=[(102, 220, 100, "active")])]
        t1 = editor(client).json()["streams"][0]
        assert t1["editor"]["base_known_ids"] == [102, 101]
        removed = t1["known_offers"][1]
        assert removed["status"] == "Removed"
        assert removed["display_share"] == 0
        assert removed["display_share_unit"] == "percentage_points"
        assert removed["last_seen_raw_share"] == 25
        assert removed["binding_id"] is None
        assert removed["last_binding_id"] == 2092367
        assert removed["pinned_share"] == 25
        assert removed["name"] == "Name v1"
        assert removed["removed_at"] is not None
        assert db_rows(db)[0][8] == first[8]
        fake.names[101] = "Name v2"
        fake.streams = [stream(pairs=[(101, 2092392, 100, "disabled"), (102, 220, 0, "active")])]
        t2 = editor(client).json()["streams"][0]
        assert t2["editor"]["base_known_ids"] == [101, 102]
        assert t2["known_offers"][0]["status"] == "Active"
        assert t2["known_offers"][0]["kt_state"] == "disabled"
        assert t2["known_offers"][0]["binding_id"] == 2092392
        assert t2["known_offers"][0]["last_binding_id"] == 2092392
        assert t2["known_offers"][0]["name"] == "Name v2"
        assert t2["known_offers"][0]["pinned_share"] == 25
        assert t2["known_offers"][0]["removed_at"] is None
        assert t2["known_offers"][0]["first_seen_at"] == t0["known_offers"][0]["first_seen_at"]
        assert (
            t0["editor"]["base_pin_revision"]
            == t1["editor"]["base_pin_revision"]
            == t2["editor"]["base_pin_revision"]
        )
        assert len({t["editor"]["base_revision"] for t in (t0, t1, t2)}) == 3
        assert len(db_rows(db)) == 2
        assert db_rows(db)[0][3] == 2092392
        assert db_rows(db)[0][8] == first[8]
        assert db_rows(db)[0][5] == 25
        assert all(item["name"] != "NEVER-RETURN-THIS" for item in t2["known_offers"])

    with TestClient(app_for(db, fake)) as client:
        restarted = editor(client).json()["streams"][0]
        assert restarted["editor"]["base_pin_revision"] == t2["editor"]["base_pin_revision"]
        assert (
            restarted["known_offers"][0]["first_seen_at"] == t0["known_offers"][0]["first_seen_at"]
        )
        post = client.post("/api/campaigns/501/streams/11/preview", json=restarted["editor"])
        assert post.status_code == 200, post.text
        assert post.json()["mode"] == "SNAPSHOT"
        assert post.json()["dirty"] is False
        assert [row["share"] for row in post.json()["rows"]] == [100, 0]


def test_two_streams_same_offer_have_independent_history_and_preview(tmp_path: Path) -> None:
    db = tmp_path / "two.db"
    migrate(db)
    fake = FakeKeitaro(
        [
            stream(sid=11, position=2, pairs=[(101, 209, 35, "disabled")]),
            stream(sid=12, position=1, pairs=[(101, 310, 165, "active")]),
            stream(sid=13, position=3, schema="redirect", pairs=[]),
        ]
    )
    with TestClient(app_for(db, fake)) as client:
        first = editor(client)
        assert first.status_code == 200, first.text
        pin(db, 11, 101, 25)
        pin(db, 12, 101, 40)
        response = editor(client).json()
        assert response["mapping_status"] == "archive_synced"
        assert response["keitaro_write_enabled"] is False
        assert response["local_archive_synced"] is True
        assert [s["id"] for s in response["streams"]] == [12, 11, 13]
        s12, s11, s13 = response["streams"]
        assert s11["known_offers"][0]["id"] == s12["known_offers"][0]["id"] == 101
        assert s11["known_offers"][0]["binding_id"] == 209
        assert s12["known_offers"][0]["binding_id"] == 310
        assert s11["known_offers"][0]["pinned_share"] == 25
        assert s12["known_offers"][0]["pinned_share"] == 40
        assert s11["editor"]["base_pin_revision"] != s12["editor"]["base_pin_revision"]
        assert s11["known_offers"][0]["kt_state"] == "disabled"
        assert s11["known_offers"][0]["status"] == "Active"
        assert s13["editor"] is None and s13["known_offers"] == []
        assert set(s11["editor"]) == set(PreviewInput.model_fields)
        assert set(s12["editor"]) == set(PreviewInput.model_fields)
        for current in (s11, s12):
            preview = client.post(
                f"/api/campaigns/501/streams/{current['id']}/preview",
                json=current["editor"],
            )
            assert preview.status_code == 200, preview.text
            assert preview.json()["mode"] == "SNAPSHOT"
            assert preview.json()["dirty"] is False
            assert preview.json()["rows"][0]["share"] in (35, 165)
    with TestClient(app_for(db, fake)) as client:
        after = editor(client).json()["streams"][:2]
        assert [r["pinned_share"] for s in after for r in s["known_offers"]] == [40, 25]
        assert [s["editor"]["base_pin_revision"] for s in after] == [
            s["editor"]["base_pin_revision"] for s in (s12, s11)
        ]
        for current in after:
            preview = client.post(
                f"/api/campaigns/501/streams/{current['id']}/preview", json=current["editor"]
            )
            assert preview.status_code == 200, preview.text
            assert preview.json()["mode"] == "SNAPSHOT"
            assert preview.json()["dirty"] is False
    assert len(db_rows(db)) == 2


def test_pin_revision_numeric_sort_null_and_independence(tmp_path: Path) -> None:
    from datetime import datetime

    from adrobot.db.archive import History

    now = datetime(2026, 11, 21)
    rows = [
        History(CAMPAIGN, 11, oid, oid + 100, "Name", share, 25, "active", now, now, None)
        for oid, share in [(10, None), (2, 25)]
    ]
    expected = {
        "version": 1,
        "campaign_id": CAMPAIGN,
        "stream_id": 11,
        "pins": [[2, 25], [10, None]],
    }
    canonical = json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = "pin-v1:" + hashlib.sha256(canonical.encode()).hexdigest()
    assert pin_revision(CAMPAIGN, 11, rows) == digest
    assert pin_revision(CAMPAIGN, 11, list(reversed(rows))) == digest
    assert pin_revision(CAMPAIGN, 12, rows) != digest
    assert pin_revision(CAMPAIGN, 11, rows[:1]) != digest
    updated = [
        rows[0],
        rows[1].__class__(CAMPAIGN, 11, 2, 102, "Name", None, 25, "active", now, now, None),
    ]
    assert pin_revision(CAMPAIGN, 11, updated) != digest


def test_archive_constraint_and_unique_key(tmp_path: Path) -> None:
    db = tmp_path / "constraints.db"
    migrate(db)
    fake = FakeKeitaro()
    with TestClient(app_for(db, fake)) as client:
        assert editor(client).status_code == 200
        engine = client.app.state.editor_coordinator.engine
        assert engine is not None
        with Session(engine) as session:
            ids = list(session.scalars(select(KnownOfferBinding)))
            assert len(ids) == 2
            assert (ids[0].campaign_id, ids[0].stream_id) == (CAMPAIGN, 11)
            assert ids[0].offer_id != ids[1].offer_id
    import sqlite3

    with sqlite3.connect(db) as connection:
        # Check valid Pin constraint independently of Pydantic validation.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE known_offer_bindings SET pinned_share=101")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE known_offer_bindings SET offer_name='' ")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO known_offer_bindings SELECT * "
                "FROM known_offer_bindings WHERE offer_id=101"
            )
