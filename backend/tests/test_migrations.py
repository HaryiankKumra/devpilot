"""Proves the Alembic migrations still describe the same schema as the models.

This is the highest-value test in the database layer. The usual failure is
silent: someone edits a model, forgets to generate a migration, and the test
suite keeps passing because it builds its schema from the models -- while
production, which is built from migrations, quietly diverges.

The comparison runs entirely offline. `alembic upgrade head --sql` renders the
migrations to PostgreSQL DDL without connecting to anything, and SQLAlchemy can
compile the model metadata to DDL for the same dialect, so the two can be
compared with no database installed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import app.db.models  # noqa: F401  (registers every model)
from app.db.base import Base

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Credentials are irrelevant: offline mode renders SQL and never connects.
OFFLINE_URL = "postgresql+psycopg://user:password@localhost:5432/devpilot"

# Built once: compiling model metadata to DDL needs a dialect, and PostgreSQL
# is the only engine these migrations target.
POSTGRES_DIALECT = postgresql.dialect()  # type: ignore[no-untyped-call]


def _split_definitions(create_table_sql: str) -> list[str]:
    """Split a CREATE TABLE body into its top-level comma-separated parts.

    A naive `split(",")` would cut inside `VARCHAR(10, 2)` and inside CHECK
    expressions, so parenthesis depth is tracked.
    """
    body = create_table_sql[create_table_sql.index("(") + 1 : create_table_sql.rindex(")")]
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return [re.sub(r"\s+", " ", part) for part in parts if part.strip()]


def _columns_and_constraints(create_table_sql: str) -> tuple[list[str], set[str]]:
    """Return column definitions in order, and constraints as an unordered set.

    Constraint *order* within CREATE TABLE carries no meaning, and Alembic emits
    them in a different order than SQLAlchemy does; comparing them as a set
    avoids a permanently-failing test that says nothing.
    """
    definitions = _split_definitions(create_table_sql)
    columns = [d for d in definitions if not d.startswith("CONSTRAINT")]
    constraints = {d for d in definitions if d.startswith("CONSTRAINT")}
    return columns, constraints


def _model_ddl(table_name: str) -> str:
    """Render one table from the ORM metadata as PostgreSQL DDL."""
    table = Base.metadata.tables[table_name]
    return str(CreateTable(table).compile(dialect=POSTGRES_DIALECT))


@pytest.fixture(scope="module")
def migration_sql() -> str:
    """The DDL the migrations produce, rendered offline."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-x",
            f"database_url={OFFLINE_URL}",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"alembic failed:\n{result.stderr}"
    return result.stdout


@pytest.fixture(scope="module")
def migration_tables(migration_sql: str) -> dict[str, str]:
    return {
        match.group(1): match.group(0)
        for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", migration_sql, re.S)
    }


class TestMigrationsMatchModels:
    def test_every_model_table_is_created(self, migration_tables: dict[str, str]) -> None:
        missing = set(Base.metadata.tables) - set(migration_tables)

        assert not missing, f"models define tables no migration creates: {sorted(missing)}"

    def test_no_migration_creates_an_unknown_table(self, migration_tables: dict[str, str]) -> None:
        # `alembic_version` is Alembic's own bookkeeping and has no model.
        extra = set(migration_tables) - set(Base.metadata.tables) - {"alembic_version"}

        assert not extra, f"migrations create tables no model defines: {sorted(extra)}"

    @pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
    def test_table_columns_match(self, table_name: str, migration_tables: dict[str, str]) -> None:
        expected, _ = _columns_and_constraints(_model_ddl(table_name))
        actual, _ = _columns_and_constraints(migration_tables[table_name])

        assert actual == expected

    @pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
    def test_table_constraints_match(
        self, table_name: str, migration_tables: dict[str, str]
    ) -> None:
        _, expected = _columns_and_constraints(_model_ddl(table_name))
        _, actual = _columns_and_constraints(migration_tables[table_name])

        assert actual == expected


class TestMigrationContent:
    def test_enables_the_pgvector_extension(self, migration_sql: str) -> None:
        """code_chunks cannot be created without it."""
        assert "CREATE EXTENSION IF NOT EXISTS vector" in migration_sql

    def test_creates_the_extension_before_the_table_that_needs_it(self, migration_sql: str) -> None:
        assert migration_sql.index("CREATE EXTENSION IF NOT EXISTS vector") < migration_sql.index(
            "CREATE TABLE code_chunks"
        )

    def test_uses_postgres_now_not_a_sqlite_default(self, migration_sql: str) -> None:
        """Regression: autogenerating against SQLite bakes in CURRENT_TIMESTAMP."""
        assert "CURRENT_TIMESTAMP" not in migration_sql
        assert "DEFAULT now()" in migration_sql

    def test_the_delivery_id_index_is_unique(self, migration_sql: str) -> None:
        """Webhook idempotency depends on this being UNIQUE, not merely indexed."""
        assert re.search(
            r"CREATE UNIQUE INDEX ix_webhook_events_delivery_id ON webhook_events \(delivery_id\)",
            migration_sql,
        )
