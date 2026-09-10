"""Rate-limit middleware.

Two different limits, because two different things are being protected.

**Authentication** gets a tight limit keyed on client IP. Login and registration
are where credential stuffing happens, and the cost of a wrong guess should grow
quickly. Argon2 already makes each attempt expensive for us as well as the
attacker, which is exactly why the endpoint needs a cap.

**Everything else** gets a generous limit keyed on the authenticated user where
there is one, falling back to IP. Keying on the user is what stops one noisy
client from consuming a shared office IP's whole allowance.

Webhooks are exempt. GitHub decides how fast it delivers, retries anything it
believes failed, and a 429 would simply become a redelivery — the endpoint is
already idempotent and cheap, and rate-limiting it would turn a burst into a
retry storm.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.rate_limit import check_rate_limit
from app.core.security import InvalidTokenError, decode_access_token
from app.db.redis import get_redis

logger = get_logger(__name__)

# Paths where a wrong guess must get expensive quickly.
AUTH_PATHS = ("/api/v1/auth/login", "/api/v1/auth/register")

# Paths that must never be limited, and why:
# - webhooks: GitHub controls the rate and retries anything refused.
# - health: an orchestrator probing every few seconds must not be throttled
#   into declaring the service dead.
EXEMPT_PREFIXES = ("/api/v1/webhooks", "/health", "/docs", "/openapi.json", "/redoc")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies per-client request limits."""

    def __init__(self, app: Callable[..., Awaitable[None]], settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        settings = self._settings

        if not settings.rate_limit_enabled or _is_exempt(request.url.path):
            return await call_next(request)

        is_auth = request.url.path in AUTH_PATHS
        limit = settings.rate_limit_auth_requests if is_auth else settings.rate_limit_requests
        window = settings.rate_limit_window_seconds
        scope = "auth" if is_auth else "api"

        result = check_rate_limit(
            get_redis(),
            identifier=_identify(request, settings),
            scope=scope,
            limit=limit,
            window_seconds=window,
        )

        if not result.allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": (
                            f"Too many requests. Please wait {result.retry_after_seconds} seconds."
                        ),
                    }
                },
                headers=result.headers(),
            )

        response = await call_next(request)
        # Headers on successful responses too, so a well-behaved client can slow
        # down before it is refused rather than after.
        for name, value in result.headers().items():
            response.headers[name] = value
        return response


def _is_exempt(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)


def _identify(request: Request, settings: Settings) -> str:
    """Who to count this request against.

    The authenticated user where there is one: keying purely on IP would let a
    shared office address exhaust everyone's allowance at once. The token is
    decoded rather than trusted, so a forged one cannot claim someone else's
    bucket.
    """
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        try:
            payload = decode_access_token(header.removeprefix("Bearer "), settings)
        except InvalidTokenError:
            pass
        else:
            return f"user:{payload.subject}"

    return f"ip:{_client_ip(request, settings)}"


def _client_ip(request: Request, settings: Settings) -> str:
    """The caller's address, honouring a proxy header only when configured to.

    `X-Forwarded-For` is trivially spoofed by the client, so trusting it
    unconditionally would let anyone reset their own rate limit by inventing an
    address. It is read only when the deployment says it sits behind a proxy
    that overwrites the header.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # Left-most entry is the original client; the rest are proxies.
            return forwarded.split(",")[0].strip()

    return request.client.host if request.client else "unknown"
