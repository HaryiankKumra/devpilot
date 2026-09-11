"""Run `alembic upgrade head` under a PostgreSQL advisory lock.

For deployments that migrate on container start rather than as a one-shot
job. A platform that starts two instances at once -- Render does, during a
zero-downtime deploy -- runs the migration twice concurrently, and the loser
dies on `relation "users" already exists`. That is the race the Compose stack
avoids with a dedicated migrate service; this is the equivalent for a single
container.

The lock is session-scoped and held on a connection of its own for as long as
Alembic runs. A second instance blocks on `pg_advisory_lock` until the first
releases it, then runs Alembic itself and finds nothing to do.

    python scripts/migrate.py
"""

from __future__ import annotations

import subprocess
import sys

import psycopg

from app.core.config import get_settings

# Any stable 64-bit integer; it only has to be the same in every instance.
MIGRATION_LOCK_KEY = 7_245_001


def main() -> int:
    # psycopg wants the plain scheme; SQLAlchemy's `+psycopg` marker is not a
    # libpq URL.
    url = str(get_settings().database_url).replace("postgresql+psycopg://", "postgresql://", 1)

    with psycopg.connect(url, autocommit=True) as lock_connection:
        print("==> waiting for the migration lock")
        lock_connection.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        print("==> lock acquired, applying migrations")
        try:
            result = subprocess.run(["alembic", "upgrade", "head"], check=False)
        finally:
            lock_connection.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
