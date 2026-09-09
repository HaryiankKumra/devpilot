"""Reusable SQLAlchemy column types."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import JSON
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB

# JSON that becomes JSONB on PostgreSQL. JSONB is stored parsed rather than as
# text, so it can be indexed and queried by key -- useful for asking questions
# of a stored webhook payload after the fact. The portable `JSON` fallback keeps
# the schema usable on other engines, including the in-memory SQLite database
# the fast tests run against.
JSONColumn = JSON().with_variant(JSONB(), "postgresql")


def enum_column_type(enum_class: type[StrEnum]) -> SAEnum:
    """Build a portable, value-backed column type for a `StrEnum`.

    Two non-obvious choices:

    `values_callable` stores the enum *value* (`"critical"`) rather than its
    Python *name* (`"CRITICAL"`), which is SQLAlchemy's default. Names are an
    implementation detail that would break every stored row if a member were
    ever renamed, and values are what the API and the LLM already speak.

    `native_enum=False` emits `VARCHAR` plus a `CHECK` constraint instead of a
    PostgreSQL `ENUM` type. Native enums need `ALTER TYPE` to add a member,
    which cannot run inside a transaction in older PostgreSQL versions and
    makes migrations awkward to roll back; a `CHECK` constraint gives the same
    guarantee and is trivial to change.
    """
    return SAEnum(
        enum_class,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda enum: [member.value for member in enum],
        length=32,
    )
