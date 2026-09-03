"""SQLAlchemy declarative metadata shared by persistent runtime models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base metadata for the PostgreSQL-backed runtime store."""
