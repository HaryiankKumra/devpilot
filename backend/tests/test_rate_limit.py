"""Tests for rate limiting and security headers."""

from __future__ import annotations

from typing import Any, cast

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.core.rate_limit import check_rate_limit
from app.db.redis import get_redis
from app.main import create_app


class FakeRedis:
    """An in-memory stand-in supporting only what the limiter uses."""

    def __init__(self, *, fail: bool = False) -> None:
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, int] = {}
        self._fail = fail

    def pipeline(self) -> FakeRedis:
        self._queued: list[tuple[str, Any]] = []
        return self

    def incr(self, key: str) -> None:
        self._queued.append(("incr", key))

    def expire(self, key: str, seconds: int) -> None:
        self._queued.append(("expire", (key, seconds)))

    def execute(self) -> list[Any]:
        if self._fail:
            raise redis.ConnectionError("redis is down")

        results: list[Any] = []
        for operation, argument in self._queued:
            if operation == "incr":
                self.counts[argument] = self.counts.get(argument, 0) + 1
                results.append(self.counts[argument])
            else:
                key, seconds = argument
                self.expiries[key] = seconds
                results.append(True)
        return results

    def ping(self) -> bool:
        return True


class TestCheckRateLimit:
    def test_allows_requests_within_the_limit(self) -> None:
        client = FakeRedis()

        for _ in range(3):
            result = check_rate_limit(
                cast(redis.Redis, client), identifier="a", scope="api", limit=3, window_seconds=60
            )

        assert result.allowed is True
        assert result.remaining == 0

    def test_refuses_the_request_past_the_limit(self) -> None:
        client = FakeRedis()

        for _ in range(3):
            check_rate_limit(
                cast(redis.Redis, client), identifier="a", scope="api", limit=3, window_seconds=60
            )
        result = check_rate_limit(
            cast(redis.Redis, client), identifier="a", scope="api", limit=3, window_seconds=60
        )

        assert result.allowed is False
        assert result.retry_after_seconds == 60

    def test_counts_each_client_separately(self) -> None:
        client = FakeRedis()

        for _ in range(3):
            check_rate_limit(
                cast(redis.Redis, client), identifier="a", scope="api", limit=3, window_seconds=60
            )
        other = check_rate_limit(
            cast(redis.Redis, client), identifier="b", scope="api", limit=3, window_seconds=60
        )

        assert other.allowed is True

    def test_counts_each_scope_separately(self) -> None:
        """Exhausting the login limit must not lock a user out of the whole API."""
        client = FakeRedis()

        for _ in range(3):
            check_rate_limit(
                cast(redis.Redis, client), identifier="a", scope="auth", limit=3, window_seconds=60
            )
        api = check_rate_limit(
            cast(redis.Redis, client), identifier="a", scope="api", limit=3, window_seconds=60
        )

        assert api.allowed is True

    def test_always_sets_an_expiry(self) -> None:
        """A key that lost its TTL would block the client permanently."""
        client = FakeRedis()

        check_rate_limit(
            cast(redis.Redis, client), identifier="a", scope="api", limit=5, window_seconds=42
        )

        assert set(client.expiries.values()) == {42}

    def test_fails_open_when_redis_is_unreachable(self) -> None:
        """A limiter that takes the API down when its own store blips has caused
        a worse outage than the one it was preventing."""
        result = check_rate_limit(
            cast(redis.Redis, FakeRedis(fail=True)),
            identifier="a",
            scope="api",
            limit=1,
            window_seconds=60,
        )

        assert result.allowed is True

    def test_headers_tell_a_client_how_to_back_off(self) -> None:
        client = FakeRedis()
        check_rate_limit(
            cast(redis.Redis, client), identifier="a", scope="api", limit=1, window_seconds=60
        )
        refused = check_rate_limit(
            cast(redis.Redis, client), identifier="a", scope="api", limit=1, window_seconds=60
        )

        headers = refused.headers()
        assert headers["X-RateLimit-Limit"] == "1"
        assert headers["X-RateLimit-Remaining"] == "0"
        assert headers["Retry-After"] == "60"


@pytest.fixture
def limited_app(db_session: Any) -> FastAPI:
    """An app with a very low limit, wired to a fake Redis."""
    from collections.abc import Iterator

    from app.api.deps import get_db

    settings = Settings(
        _env_file=None,
        environment=Environment.CI,
        rate_limit_enabled=True,
        rate_limit_requests=3,
        rate_limit_auth_requests=2,
        rate_limit_window_seconds=60,
    )
    application = create_app(settings)

    def override_get_db() -> Iterator[Any]:
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    return application


class TestMiddleware:
    def test_refuses_past_the_limit_with_a_json_error(
        self, limited_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeRedis()
        monkeypatch.setattr("app.api.middleware_rate_limit.get_redis", lambda: fake)

        with TestClient(limited_app) as client:
            for _ in range(3):
                assert client.get("/api/v1/reviews").status_code in (200, 401)
            response = client.get("/api/v1/reviews")

        assert response.status_code == 429
        assert response.json()["error"]["code"] == "rate_limited"
        assert response.headers["Retry-After"] == "60"

    def test_health_is_never_limited(
        self, limited_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An orchestrator probing every few seconds must not be throttled into
        declaring the service dead."""
        fake = FakeRedis()
        monkeypatch.setattr("app.api.middleware_rate_limit.get_redis", lambda: fake)

        with TestClient(limited_app) as client:
            for _ in range(20):
                assert client.get("/health").status_code == 200

    def test_webhooks_are_never_limited(
        self, limited_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 429 would just become a GitHub redelivery -- a retry storm, not a
        reduction in load."""
        fake = FakeRedis()
        monkeypatch.setattr("app.api.middleware_rate_limit.get_redis", lambda: fake)

        with TestClient(limited_app) as client:
            for _ in range(20):
                # Unsigned, so 401 -- but never 429.
                assert client.post("/api/v1/webhooks/github", json={}).status_code == 401

    def test_successful_responses_carry_the_remaining_count(
        self, limited_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So a well-behaved client can slow down before it is refused."""
        fake = FakeRedis()
        monkeypatch.setattr("app.api.middleware_rate_limit.get_redis", lambda: fake)

        with TestClient(limited_app) as client:
            response = client.get("/api/v1/reviews")

        assert "X-RateLimit-Remaining" in response.headers


class TestSecurityHeaders:
    @pytest.fixture
    def client(self) -> TestClient:
        app = create_app(
            Settings(_env_file=None, environment=Environment.CI, rate_limit_enabled=False)
        )
        return TestClient(app)

    def test_forbids_content_type_sniffing(self, client: TestClient) -> None:
        """Without it, a JSON response containing attacker-controlled text can
        be sniffed as HTML and executed."""
        assert client.get("/health").headers["X-Content-Type-Options"] == "nosniff"

    def test_forbids_framing(self, client: TestClient) -> None:
        assert client.get("/health").headers["X-Frame-Options"] == "DENY"

    def test_sends_no_referrer(self, client: TestClient) -> None:
        """URLs contain resource ids; they should not leak to third parties."""
        assert client.get("/health").headers["Referrer-Policy"] == "no-referrer"

    def test_sets_a_restrictive_content_security_policy(self, client: TestClient) -> None:
        policy = client.get("/health").headers["Content-Security-Policy"]

        assert "default-src 'none'" in policy
        assert "frame-ancestors 'none'" in policy

    def test_hsts_is_off_by_default(self, client: TestClient) -> None:
        """Meaningless over plain HTTP, and locally it would pin localhost to
        HTTPS in the developer's browser for a year."""
        assert "Strict-Transport-Security" not in client.get("/health").headers

    def test_hsts_can_be_enabled(self) -> None:
        app = create_app(
            Settings(
                _env_file=None,
                environment=Environment.CI,
                rate_limit_enabled=False,
                enable_hsts=True,
            )
        )
        with TestClient(app) as client:
            assert "max-age=" in client.get("/health").headers["Strict-Transport-Security"]


class TestRedisFixtureIsolation:
    def test_the_real_client_factory_is_still_cached(self) -> None:
        assert get_redis() is get_redis()
