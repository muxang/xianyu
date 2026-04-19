"""Declarative base for all ORM models.

Models in each module subclass this so Alembic's autogenerate sees a
single MetaData object via ``Base.metadata``.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""
