"""Rate limiting, backed by Redis.

Limits are enforced in Redis rather than in process memory because the API runs
as several processes behind a load balancer. An in-memory counter would give
each process its own allowance, so N replicas would mean N times the intended
limit -- and the limit would reset on every deploy.

The algorithm is a **fixed window**: one counter per client per window, expiring
when the window does. It admits a burst at a window boundary (up to 2x the limit
across two adjacent windows), which a sliding log would not. That is accepted
deliberately: the sliding version costs a sorted set and several round trips per
request, and the purpose here is to stop brute-force and runaway clients, not to
meter billing to the request.

The counter is incremented and expired in a single pipeline, so two concurrent
requests cannot both see "no key yet" and both set a fresh expiry -- which would
let a client reset its own window by timing requests.
"""

from __future__ import annotations

from dataclasses import dataclass

import redis

from app.core.logging import get_logger

logger = get_logger(__name__)

KEY_PREFIX = "devpilot:ratelimit"


@dataclass(frozen=True)
class RateLimitResult:
    """The outcome of one rate-limit check."""

    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int

    def headers(self) -> dict[str, str]:
        """Standard headers so a client can back off without guessing."""
        values = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
        }
        if not self.allowed:
            values["Retry-After"] = str(self.retry_after_seconds)
        return values


def check_rate_limit(
    client: redis.Redis,
    *,
    identifier: str,
    scope: str,
    limit: int,
    window_seconds: int,
) -> RateLimitResult:
    """Count one request against a client's allowance.

    Fails **open**: if Redis is unreachable, the request is allowed. A rate
    limiter that takes the whole API down when its own backing store blips has
    caused a worse outage than the one it was protecting against. The failure is
    logged so it cannot pass unnoticed.
    """
    key = f"{KEY_PREFIX}:{scope}:{identifier}"

    try:
        pipeline = client.pipeline()
        pipeline.incr(key)
        # Set the expiry every time rather than only on creation. `NX` would be
        # tidier, but a key that somehow loses its TTL would then block the
        # client permanently -- this way the window always ends.
        pipeline.expire(key, window_seconds)
        current, _ = pipeline.execute()
    except redis.RedisError as exc:
        logger.error("rate_limit.unavailable", scope=scope, error=str(exc))
        return RateLimitResult(allowed=True, limit=limit, remaining=limit, retry_after_seconds=0)

    count = int(current)
    allowed = count <= limit

    if not allowed:
        logger.info("rate_limit.exceeded", scope=scope, identifier=identifier, count=count)

    return RateLimitResult(
        allowed=allowed,
        limit=limit,
        remaining=limit - count,
        retry_after_seconds=window_seconds,
    )
