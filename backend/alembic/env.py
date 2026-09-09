"""Alembic environment.

Two things differ from the stock template, both deliberate:

* The database URL comes from DevPilot's `Settings`, not from `alembic.ini`.
  Keeping one source of configuration means a migration can never be applied to
  a different database than the application talks to, and no credential is ever
  written into a tracked file.
* `target_metadata` is DevPilot's `Base.metadata`, with every model imported, so
  autogenerate sees the whole schema.

Migrations target PostgreSQL only. The test suite builds its schema directly
from the ORM metadata rather than by running migrations, so there is no need for
SQLite batch-mode rendering here -- and leaving it out keeps the generated SQL
free of workarounds that would never run in production.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing the model registry has the side effect of registering every table on
# `Base.metadata`; without it autogenerate would produce an empty migration.
import app.db.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    """Return the URL to migrate.

    A URL passed with `-x database_url=...` wins, which is how CI points a
    migration at a throwaway database; otherwise the application settings apply.
    """
    override = context.get_x_argument(as_dictionary=True).get("database_url")
    if override:
        return override
    return str(get_settings().database_url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it.

    Used by `alembic upgrade head --sql`, which is how the migrations can be
    reviewed (or handed to a DBA) without connecting to anything.
    """
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Render the schema change as a transaction so a failure rolls back.
        transactional_ddl=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect and apply migrations."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        # A migration is a short-lived, single-connection operation; pooling
        # would keep connections open after it finishes.
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Detect column type changes as well as added and removed columns;
            # off by default, and its absence silently misses a widened column.
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
