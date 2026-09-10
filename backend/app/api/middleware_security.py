"""Security response headers.

Each header here closes a specific attack, and each is set for a reason worth
being able to state:

`X-Content-Type-Options: nosniff` stops a browser second-guessing a declared
content type. Without it, a JSON response containing attacker-controlled text
can be sniffed as HTML and executed.

`X-Frame-Options: DENY` prevents the app being embedded in an invisible frame
over a decoy page, so a click lands on a real control -- clickjacking.

`Referrer-Policy` keeps full URLs, which contain resource ids, out of the
Referer header sent to third-party sites.

`Content-Security-Policy` is the broad one: it constrains where scripts may come
from, so an injected `<script src>` has nowhere to load from.

`Strict-Transport-Security` is off by default because it is meaningless over
plain HTTP and actively harmful locally -- it would pin `localhost` to HTTPS in
the developer's browser for a year.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import Settings

# The API serves JSON, not documents, so almost everything can be denied. The
# `/docs` page is the one exception and is disabled in production anyway.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)

HSTS_VALUE = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds hardening headers to every response."""

    def __init__(self, app: Callable[..., Awaitable[None]], settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # Explicitly surrender capabilities the API never uses, so a future
        # embedded page cannot silently acquire them.
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )

        # The interactive docs need inline scripts and a CDN, so the strict
        # policy would break the page it is meant to protect.
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)

        if self._settings.enable_hsts:
            response.headers.setdefault("Strict-Transport-Security", HSTS_VALUE)

        return response
