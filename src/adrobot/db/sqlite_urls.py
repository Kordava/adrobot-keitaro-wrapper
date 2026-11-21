"""Canonical SQLite file identity shared by Alembic and the runtime archive.

`database` is decoded exactly once from the *original* settings URL, not
through SQLAlchemy's version-dependent URL string representation.
"""

import re
from pathlib import Path
from urllib.parse import quote, unquote_to_bytes, urlsplit

from sqlalchemy.engine import URL

_BAD_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")


class InvalidSqliteURL(ValueError):
    """The configured archive is not an ordinary filesystem SQLite database."""


def sqlite_file_path(database_url: str) -> Path:
    """Return the one absolute path represented by a file SQLite URL.

    The URL database portion uses percent-encoding: `%25` denotes `%`, and
    `%2525` denotes the literal filename sequence `%25`.
    """
    try:
        parsed = urlsplit(database_url)
    except ValueError as exc:
        raise InvalidSqliteURL("Invalid SQLite archive URL") from exc
    if (
        parsed.scheme not in ("sqlite", "sqlite+pysqlite")
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise InvalidSqliteURL("Archive must use a filesystem SQLite URL without query")
    encoded = parsed.path[1:]
    if not encoded or _BAD_ESCAPE.search(encoded):
        raise InvalidSqliteURL("Invalid SQLite archive filename")
    try:
        filename = unquote_to_bytes(encoded).decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise InvalidSqliteURL("Invalid UTF-8 SQLite archive filename") from exc
    if not filename or "\x00" in filename or filename == ":memory:" or filename.startswith("file:"):
        raise InvalidSqliteURL("Archive requires a real SQLite file")
    return Path(filename).resolve(strict=False)


def migration_sqlite_url(database_url: str) -> URL:
    """DDL is the only operation allowed to create the configured DB file."""
    return URL.create("sqlite+pysqlite", database=str(sqlite_file_path(database_url)))


def runtime_sqlite_url(database_url: str) -> URL:
    """Open exactly the existing file: mode=rw prevents implicit creation."""
    path = sqlite_file_path(database_url)
    return URL.create(
        "sqlite+pysqlite",
        database=f"file:{quote(path.as_posix(), safe='/')}",
        query={"mode": "rw", "uri": "true"},
    )
