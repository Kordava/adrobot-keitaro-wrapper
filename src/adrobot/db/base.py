"""SQLAlchemy metadata for the known-offer archive and Alembic."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Project's shared declarative metadata."""
