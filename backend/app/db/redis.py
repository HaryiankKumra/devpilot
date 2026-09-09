"""Redis client factory.

Redis serves three roles in DevPilot: Celery broker, Celery result backend, and
the store behind API rate limiting. A single lazily-created client is shared by
the process.
"""

from __future__ import annotations

from functools import lru_cache

import redis

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    """Return the process-wide Redis client."""
    settings = get_settings()
    return redis.Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        # Same reasoning as the database timeouts: an unreachable Redis must
        # surface as an error quickly, not as a stalled request.
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
    )
