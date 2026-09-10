"""Shared pytest fixtures.

The suite builds its own `Settings` and passes them to `create_app()`, and
`_hermetic_environment` below hides every `DEVPILOT_*` variable, so it depends
on neither a developer's local `.env` nor whatever the surrounding shell or CI
runner happens to export.

Database tests run against an in-memory SQLite database rather than PostgreSQL,
which keeps them fast and runnable with no services installed. The tradeoff is
real and worth stating: SQLite is not PostgreSQL, so these tests verify
application behaviour (constraints, cascades, query logic) rather than
PostgreSQL-specific behaviour. The parts that genuinely need PostgreSQL --
pgvector similarity search above all -- are marked `integration` and skipped
here. `tests/test_migrations.py` separately proves the migrations still match
the models, which is the drift these tests could otherwise hide.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, Table, create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool, StaticPool

from app.api.deps import get_db
from app.core.config import Environment, Settings

# Imported for its import side effect: registering every model on
# `Base.metadata`. Aliased because the bare `import app.db.models` form binds
# the name `app`, which would collide with the `app` fixture below.
from app.db import models as _models  # noqa: F401
from app.db.base import Base
from app.db.models.user import User
from app.db.redis import get_redis
from app.main import create_app

# `code_chunks` stores a pgvector column, which has no SQLite equivalent.
SQLITE_UNSUPPORTED_TABLES = frozenset({"code_chunks"})


@pytest.fixture(scope="session", autouse=True)
def _hermetic_environment() -> Iterator[None]:
    """Hide every `DEVPILOT_*` variable from the tests.

    `Settings(_env_file=None)` stops pydantic-settings reading `.env`, but it
    still reads `os.environ` -- so a test asserting on a *default* actually
    asserts on whatever the surrounding shell happens to export. Two tests in
    `test_config.py` did exactly that: they passed everywhere until CI set
    `DEVPILOT_SECRET_KEY` at the workflow level, and then failed claiming the
    production secret guard was broken when the guard was fine.

    That is a bad failure to debug, because the test is correct, the code is
    correct, and only the environment differs. Clearing the variables for the
    whole session makes the suite depend on nothing but its own fixtures.

    `DEVPILOT_TEST_DATABASE_URL` is unaffected: it is read into
    `INTEGRATION_DATABASE_URL` at import time, before this fixture runs, so the
    integration tests still find the database CI points them at.
    """
    saved = {name: value for name, value in os.environ.items() if name.startswith("DEVPILOT_")}
    for name in saved:
        del os.environ[name]

    try:
        yield
    finally:
        os.environ.update(saved)


def portable_tables() -> list[Table]:
    """Every table that can be created on SQLite."""
    return [
        table
        for name, table in Base.metadata.tables.items()
        if name not in SQLITE_UNSUPPORTED_TABLES
    ]


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Deterministic settings for tests."""
    return Settings(
        environment=Environment.CI,
        debug=False,
        cors_origins=["http://localhost:5173"],
        # Off for the suite at large. Left on, every test would share one Redis
        # window and throttle the ones that ran after it -- and the tests would
        # depend on Redis being up. `tests/test_rate_limit.py` enables it
        # explicitly against a fake.
        rate_limit_enabled=False,
    )


@pytest.fixture
def db_engine() -> Iterator[Engine]:
    """An isolated in-memory database, rebuilt for each test."""
    engine = create_engine(
        "sqlite://",
        # An in-memory SQLite database belongs to its connection, so a normal
        # pool would hand out a different (empty) database to the next caller.
        # StaticPool keeps one connection for the whole engine.
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(connection: Any, _record: Any) -> None:
        # SQLite ignores foreign keys unless asked not to, which would silently
        # make every cascade and referential-integrity test pass.
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine, tables=portable_tables())
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """A session bound to the throwaway database."""
    with Session(db_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def app(settings: Settings, db_session: Session) -> FastAPI:
    """An application wired to the test database.

    `get_db` is overridden to hand back the *same* session the test holds, so a
    test can inspect rows a request wrote without opening a second connection.
    """
    application = create_app(settings)

    def override_get_db() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """HTTP client bound to the test application."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def unstubbed_client(settings: Settings) -> Iterator[TestClient]:
    """A client with no dependency overrides at all.

    Used by tests that must prove an endpoint works without touching the
    database or Redis, which an override would quietly hide.
    """
    with TestClient(create_app(settings)) as test_client:
        yield test_client


# --- Domain fixtures ---------------------------------------------------------

TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def registered_user(db_session: Session) -> User:
    """An active account with a known password."""
    from app.services.auth import register_user

    user = register_user(
        db_session,
        email="developer@example.com",
        password=TEST_PASSWORD,
        full_name="Test Developer",
    )
    db_session.commit()
    return user


@pytest.fixture
def auth_headers(client: TestClient, registered_user: User) -> dict[str, str]:
    """Authorization header carrying a valid token for `registered_user`."""
    response = client.post(
        "/api/v1/auth/login",
        json={"email": registered_user.email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(autouse=True)
def _reset_redis_cache() -> Iterator[None]:
    """Stop a cached Redis client leaking between tests."""
    yield
    get_redis.cache_clear()


# --- Integration fixtures (real PostgreSQL) ---------------------------------
# `code_chunks` holds a pgvector column with no SQLite equivalent, so anything
# touching retrieval needs the real database. These tests are marked
# `integration` and skip cleanly when it is not running, so the default suite
# stays runnable with nothing installed.

INTEGRATION_DATABASE_URL = os.environ.get(
    "DEVPILOT_TEST_DATABASE_URL",
    "postgresql+psycopg://devpilot:devpilot@localhost:5432/devpilot",
)


@pytest.fixture(scope="session")
def integration_engine() -> Iterator[Engine]:
    """A connection to a real PostgreSQL, or a skip if there is not one."""
    engine = create_engine(INTEGRATION_DATABASE_URL, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            has_vector = connection.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).first()
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL is not reachable for integration tests: {exc}")

    if has_vector is None:
        pytest.skip("The `vector` extension is not installed in the test database.")

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def pg_session(integration_engine: Engine) -> Iterator[Session]:
    """A session whose work is always rolled back.

    The integration database is the same one the developer is using, so every
    test runs inside a transaction that is discarded. Nothing a test writes
    survives it.
    """
    connection = integration_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
