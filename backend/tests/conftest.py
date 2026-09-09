"""Shared pytest fixtures.

The test suite builds its own `Settings` and passes it to `create_app()`, so it
never depends on a developer's local `.env`. Tests in this file require no
running PostgreSQL or Redis; anything that does is marked `integration`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Deterministic settings for tests."""
    return Settings(
        environment=Environment.CI,
        debug=False,
        cors_origins=["http://localhost:5173"],
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """A freshly wired application, isolated per test."""
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """HTTP client bound to the test application."""
    with TestClient(app) as test_client:
        yield test_client
