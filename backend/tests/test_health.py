"""Tests for the liveness and readiness endpoints."""

from __future__ import annotations

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.deps import get_db
from app.db.redis import get_redis


class _StubSession:
    """Stands in for a SQLAlchemy session; records or refuses the probe query."""

    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy

    def execute(self, statement: object) -> object:
        if not self.healthy:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        return object()


class _StubRedis:
    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy

    def ping(self) -> bool:
        if not self.healthy:
            raise redis.ConnectionError("connection refused")
        return True


def _override(app: FastAPI, *, db_healthy: bool, redis_healthy: bool) -> None:
    app.dependency_overrides[get_db] = lambda: _StubSession(healthy=db_healthy)
    app.dependency_overrides[get_redis] = lambda: _StubRedis(healthy=redis_healthy)


class TestLiveness:
    def test_returns_ok_without_touching_dependencies(self, unstubbed_client: TestClient) -> None:
        # This client has no dependency overrides, so a database or Redis
        # dependency creeping into liveness would fail here.
        response = unstubbed_client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["environment"] == "ci"
        assert body["version"]

    def test_echoes_a_request_id_header(self, unstubbed_client: TestClient) -> None:
        response = unstubbed_client.get("/health")

        assert response.headers["X-Request-ID"]

    def test_reuses_an_upstream_request_id(self, unstubbed_client: TestClient) -> None:
        response = unstubbed_client.get("/health", headers={"X-Request-ID": "abc-123"})

        assert response.headers["X-Request-ID"] == "abc-123"


class TestReadiness:
    def test_reports_ok_when_all_dependencies_answer(
        self, app: FastAPI, client: TestClient
    ) -> None:
        _override(app, db_healthy=True, redis_healthy=True)

        response = client.get("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert {d["name"] for d in body["dependencies"]} == {"postgres", "redis"}
        assert all(d["healthy"] for d in body["dependencies"])

    @pytest.mark.parametrize(
        ("db_healthy", "redis_healthy", "failing"),
        [
            (False, True, "postgres"),
            (True, False, "redis"),
        ],
    )
    def test_returns_503_when_a_dependency_is_down(
        self,
        app: FastAPI,
        client: TestClient,
        db_healthy: bool,
        redis_healthy: bool,
        failing: str,
    ) -> None:
        _override(app, db_healthy=db_healthy, redis_healthy=redis_healthy)

        response = client.get("/health/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        unhealthy = [d for d in body["dependencies"] if not d["healthy"]]
        assert [d["name"] for d in unhealthy] == [failing]

    def test_probes_every_dependency_even_after_a_failure(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """One call should report the whole picture, not just the first problem."""
        _override(app, db_healthy=False, redis_healthy=False)

        body = client.get("/health/ready").json()

        assert len(body["dependencies"]) == 2
        assert not any(d["healthy"] for d in body["dependencies"])

    def test_does_not_leak_connection_details_in_errors(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """Driver errors can embed credentials; only the exception type is exposed."""
        _override(app, db_healthy=False, redis_healthy=True)

        body = client.get("/health/ready").json()

        detail = next(d for d in body["dependencies"] if d["name"] == "postgres")["detail"]
        assert detail == "OperationalError"
