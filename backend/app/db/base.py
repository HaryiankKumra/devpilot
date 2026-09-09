"""Declarative base and shared column mixins.

Alembic imports `Base` from here to discover metadata for migrations, so every
model module must be imported by `app.db.models` before Alembic runs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Naming every constraint explicitly means Alembic can always refer to one by
# name. Without this, PostgreSQL invents names for unnamed constraints and a
# later migration that wants to drop one has nothing reliable to target.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for DevPilot ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """A UUID primary key generated in Python.

    UUIDs rather than sequential integers because these ids appear in URLs and
    are returned to API clients: a sequential id leaks how many rows exist and
    lets a client walk other users' records by guessing neighbours.

    Generated client-side rather than by a database default so the application
    knows the id before the INSERT, which lets it build related rows in one
    flush instead of round-tripping to read the id back.
    """

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """Creation and modification timestamps, maintained by the database.

    `server_default` and `onupdate` keep these correct even for rows written by
    a migration or by hand in psql, which a Python-side default would miss.
    Stored with a timezone because a worker, the API and the database may sit
    in different ones.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
