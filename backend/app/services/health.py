"""Health-check business logic.

Kept out of the route so the probing rules are unit-testable without HTTP, and
so the same checks can later be reused by the worker's own health endpoint.
"""

from __future__ import annotations

import time

import redis
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.schemas.health import DependencyCheck, HealthStatus, ReadinessResponse

logger = get_logger(__name__)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def probe_database(session: Session) -> DependencyCheck:
    """Run the cheapest possible query to prove the connection works."""
    started = time.perf_counter()
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("healthcheck.postgres.failed", error=str(exc))
        return DependencyCheck(
            name="postgres",
            healthy=False,
            # Driver errors can embed the connection URL (and therefore the
            # password), so report the exception type only.
            detail=type(exc).__name__,
            latency_ms=_elapsed_ms(started),
        )
    return DependencyCheck(name="postgres", healthy=True, latency_ms=_elapsed_ms(started))


def probe_redis(client: redis.Redis) -> DependencyCheck:
    """PING Redis."""
    started = time.perf_counter()
    try:
        client.ping()
    except redis.RedisError as exc:
        logger.warning("healthcheck.redis.failed", error=str(exc))
        return DependencyCheck(
            name="redis",
            healthy=False,
            detail=type(exc).__name__,
            latency_ms=_elapsed_ms(started),
        )
    return DependencyCheck(name="redis", healthy=True, latency_ms=_elapsed_ms(started))


def check_readiness(session: Session, client: redis.Redis) -> ReadinessResponse:
    """Probe every backing service DevPilot cannot serve traffic without.

    Both probes always run, even if the first fails, so one call reports the
    complete picture instead of only the first problem.

    They run sequentially, so the worst-case response time is the *sum* of the
    configured connect timeouts: ~5s under Docker Compose, where each service
    name resolves to one address. It roughly doubles when pointed at
    `localhost`, which resolves to both `::1` and `127.0.0.1`. Keeping this
    sequential avoids running a thread pool inside the request handler for a
    saving that only matters when the system is already broken.
    """
    checks = [probe_database(session), probe_redis(client)]
    status = HealthStatus.OK if all(c.healthy for c in checks) else HealthStatus.DEGRADED
    return ReadinessResponse(status=status, dependencies=checks)
