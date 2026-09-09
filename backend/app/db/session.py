"""Database engine and session management.

DevPilot uses **synchronous** SQLAlchemy 2.0 everywhere. Celery workers are
synchronous processes, and the same data-access code is shared by the API and
the workers; keeping one sync code path avoids maintaining parallel async and
sync repositories. FastAPI runs blocking `def` endpoints in a thread pool, and
the API itself is I/O-light because the expensive work (diffing, embedding,
LLM calls) is handed off to workers.

The engine is created lazily rather than at import time so that importing this
module never opens sockets or requires valid credentials -- Alembic and the
test suite both import it before a database necessarily exists.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


def build_engine_kwargs(settings: Settings) -> dict[str, Any]:
    """Return the pool configuration for an engine built from `settings`.

    Split out from `build_engine` so the policy below can be asserted directly
    in tests without reaching into SQLAlchemy's internals.

    Every timeout here exists because its absence turns a dead dependency into
    a hung request: without `connect_timeout` the readiness probe waits out the
    OS TCP retry budget instead of answering 503, and without
    `statement_timeout` one wedged query occupies a pool slot indefinitely.
    """
    return {
        # Verify a pooled connection is still alive before handing it out;
        # without this, connections dropped by Postgres or a proxy surface as
        # apparently random errors on the next request that reuses them.
        "pool_pre_ping": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        # Cap the wait for a free pool slot instead of queueing forever.
        "pool_timeout": settings.db_pool_timeout_seconds,
        # Recycle connections before a proxy or Postgres decides to close them.
        "pool_recycle": 1800,
        "connect_args": {
            "connect_timeout": settings.db_connect_timeout_seconds,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
        },
        "echo": False,
    }


def build_engine(settings: Settings) -> Engine:
    """Create a connection pool configured to fail fast rather than hang."""
    return create_engine(str(settings.database_url), **build_engine_kwargs(settings))


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use."""
    return build_engine(get_settings())


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """Return the process-wide session factory."""
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        # Keep attributes readable after commit; handlers routinely serialise an
        # object they just wrote, and re-fetching it would cost a round trip.
        expire_on_commit=False,
    )


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional session for non-HTTP callers (Celery, scripts).

    Commits on success, rolls back on failure, always closes.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
