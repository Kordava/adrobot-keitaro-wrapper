"""SQLAlchemy metadata for future archive model (await OPEN-03 binding identity)."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Project's shared declarative metadata."""
