"""SQLAlchemy declarative base isolated from the Agent Runtime."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base metadata for future persistent SessionStore implementations."""
