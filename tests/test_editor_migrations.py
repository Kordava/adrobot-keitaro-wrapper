"""Actual Alembic migration and runtime same-file SQLite invariants."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from b1_support import ROOT, migrate
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from adrobot.db.archive import create_archive_engine
from adrobot.db.sqlite_urls import (
    InvalidSqliteURL,
    migration_sqlite_url,
    runtime_sqlite_url,
    sqlite_file_path,
)


@pytest.mark.parametrize(
    ("filename", "encoded_filename"),
    [
        ("archive%.db", "archive%25.db"),
        ("archive%25.db", "archive%2525.db"),
        ("archive#.db", "archive%23.db"),
        ("archive.db?timeout=0", "archive.db%3Ftimeout%3D0"),
        ("archive%(token)s.db", "archive%25%28token%29s.db"),
        (
            "archive с пробелом.db",
            "archive%20%D1%81%20%D0%BF%D1%80%D0%BE%D0%B1%D0%B5%D0%BB%D0%BE%D0%BC.db",
        ),
    ],
)
def test_alembic_and_runtime_open_exact_independent_file(
    tmp_path: Path, filename: str, encoded_filename: str
) -> None:
    parent = tmp_path / "dir#with%chars"
    parent.mkdir()
    expected_file = parent / filename  # oracle not derived from any URL parser
    # The parent and the target are encoded independently; no double decoding.
    encoded_parent = quote(str(parent), safe="/")
    database_url = "sqlite:///" + encoded_parent + "/" + encoded_filename
    assert sqlite_file_path(database_url) == expected_file
    engine = create_archive_engine(database_url)
    with pytest.raises(OperationalError):
        with engine.connect():
            pass
    engine.dispose()
    assert not expected_file.exists()
    assert not list(parent.iterdir())

    migrate(expected_file, database_url=database_url)
    assert expected_file.is_file()
    # No alternate truncated file (`archive.db` is a known SQLAlchemy 2.0 trap).
    assert list(parent.iterdir()) == [expected_file]
    with sqlite3.connect(expected_file) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "a001_known_offer_bindings",
        )
        names = {col[1] for col in connection.execute("PRAGMA table_info(known_offer_bindings)")}
        assert {
            "campaign_id",
            "stream_id",
            "offer_id",
            "last_binding_id",
            "pinned_share",
            "first_seen_at",
            "last_seen_at",
            "removed_at",
        } <= names
        connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES ('KEEP')")
    migrate(expected_file, database_url=database_url)  # repeat upgrade is non-destructive
    engine = create_archive_engine(database_url)
    with engine.connect() as connection:
        assert Path(connection.exec_driver_sql("PRAGMA database_list").one()[2]) == expected_file
        assert connection.exec_driver_sql("SELECT value FROM sentinel").scalar_one() == "KEEP"
    engine.dispose()
    assert list(parent.iterdir()) == [expected_file]

    expected_file.unlink()
    engine = create_archive_engine(database_url)
    with pytest.raises(OperationalError):
        with engine.connect():
            pass
    engine.dispose()
    assert not expected_file.exists()
    assert not list(parent.iterdir())


@pytest.mark.parametrize(
    "invalid",
    [
        "sqlite:///:memory:",
        "sqlite:///",
        "sqlite:///file:archive.db",
        "sqlite:///archive.db?timeout=0",
        "sqlite:////tmp/archive.db#fragment",
        "sqlite://host/archive.db",
        "postgresql:///archive.db",
        "sqlite:///archive%notvalid.db",
        "sqlite:///archive%FF.db",
        "sqlite:///archive%00bad.db",
    ],
)
def test_sqlite_file_path_rejects_unsafe_or_ambiguous_input(invalid: str) -> None:
    with pytest.raises(InvalidSqliteURL):
        sqlite_file_path(invalid)


def test_relative_settings_path_resolves_without_url_double_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    expected_file = tmp_path / "a#b%25.db"
    assert sqlite_file_path("sqlite:///./a%23b%2525.db") == expected_file
    assert migration_sqlite_url("sqlite:///./a%23b%2525.db").database == str(expected_file)
    assert runtime_sqlite_url("sqlite:///./a%23b%2525.db").query == {"mode": "rw", "uri": "true"}
    assert not expected_file.exists()


def test_begin_immediate_is_physical_before_select_and_holds_one_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "isolation.db"
    migrate(db)
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE counter (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO counter VALUES (1)")
    engine = create_archive_engine("sqlite:///" + str(db))
    with Session(engine) as session:
        with session.begin():
            connection = session.connection()  # force BEGIN IMMEDIATE, not just Session.begin()
            dbapi = connection.connection.driver_connection
            assert isinstance(dbapi, sqlite3.Connection)
            assert dbapi.in_transaction is True
            first = session.execute(text("SELECT value FROM counter")).scalar_one()
            with sqlite3.connect(db, timeout=0.02) as competing:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    competing.execute("UPDATE counter SET value=2")
            second = session.execute(text("SELECT value FROM counter")).scalar_one()
            assert (first, second) == (1, 1)
    with sqlite3.connect(db) as competing:
        competing.execute("UPDATE counter SET value=3")
    with Session(engine) as session:
        with session.begin():
            assert session.execute(text("SELECT value FROM counter")).scalar_one() == 3
    engine.dispose()


def test_alembic_offline_mode_does_not_create_file(tmp_path: Path) -> None:
    db = tmp_path / "offline%23.db"
    url = "sqlite:///" + quote(str(db), safe="/")
    env = dict(os.environ, ADROBOT_DATABASE_URL=url)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "known_offer_bindings" in result.stdout
    assert not db.exists()
