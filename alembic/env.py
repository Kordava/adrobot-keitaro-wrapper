"""Alembic uses the canonical filesystem path, with a create-capable URL object."""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from adrobot.config import get_settings
from adrobot.db.base import Base
from adrobot.db.models import KnownOfferBinding  # noqa: F401 - registers metadata
from adrobot.db.sqlite_urls import migration_sqlite_url
from alembic import context

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Do not round-trip database URL through ConfigParser or SQLAlchemy's version-
# dependent string parser. In particular, a filename containing literal '?' must
# not be reinterpreted as `?timeout=...` on SQLAlchemy 2.0.
migration_url = migration_sqlite_url(get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without opening the database file."""
    context.configure(url=migration_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Migrations are explicitly allowed to create the file; runtime is not."""
    section = dict(config.get_section(config.config_ini_section, {}))
    section["sqlalchemy.url"] = migration_url
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
