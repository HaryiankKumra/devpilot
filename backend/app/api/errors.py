"""Maps integration failures onto HTTP responses.

This lives in the API layer rather than in `core.exceptions` on purpose. The
integration code raises exceptions that describe *what GitHub did*; deciding
which status code a browser should see is an HTTP concern, and putting the
mapping here keeps `core` from having to know that `app.integrations` exists.

The status codes chosen say whose fault the failure is:

* `404` -- the resource genuinely is not there for this installation.
* `502` -- DevPilot could not get a usable answer out of GitHub. The client did
  nothing wrong and cannot fix it by changing the request.
* `503` with `Retry-After` -- temporarily unavailable, come back later. Used for
  rate limits and for a deployment with no GitHub credentials configured.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.integrations.github.exceptions import (
    GitHubAuthenticationError,
    GitHubConfigurationError,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubTransientError,
)

logger = get_logger(__name__)


def _body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def register_integration_exception_handlers(app: FastAPI) -> None:
    """Attach handlers for third-party integration failures."""

    @app.exception_handler(GitHubNotFoundError)
    async def handle_not_found(_: Request, exc: GitHubNotFoundError) -> JSONResponse:
        # GitHub answers 404 for private resources the caller may not be allowed
        # to know about, so "missing" and "forbidden" are indistinguishable here.
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_body("github_not_found", str(exc)),
        )

    @app.exception_handler(GitHubRateLimitError)
    async def handle_rate_limit(_: Request, exc: GitHubRateLimitError) -> JSONResponse:
        logger.warning("github.rate_limited_response", retry_after=exc.retry_after_seconds)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_body(
                "github_rate_limited",
                "GitHub's rate limit is exhausted. Please try again shortly.",
            ),
            # Tell the client exactly how long to wait rather than making it guess.
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    @app.exception_handler(GitHubConfigurationError)
    async def handle_configuration(_: Request, exc: GitHubConfigurationError) -> JSONResponse:
        # A deployment problem, not a client one. The message is safe to show:
        # it names environment variables, never their values.
        logger.error("github.not_configured", detail=str(exc))
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_body("github_not_configured", str(exc)),
        )

    @app.exception_handler(GitHubAuthenticationError)
    async def handle_authentication(_: Request, exc: GitHubAuthenticationError) -> JSONResponse:
        # Our credentials, not the caller's, so this is never a 401 to them.
        logger.error("github.authentication_failed", detail=str(exc))
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=_body(
                "github_access_denied",
                "DevPilot could not access that resource on GitHub. The App may need "
                "to be reinstalled or granted access.",
            ),
        )

    @app.exception_handler(GitHubTransientError)
    async def handle_transient(_: Request, exc: GitHubTransientError) -> JSONResponse:
        logger.warning("github.transient_failure", detail=str(exc))
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=_body("github_unavailable", "GitHub is not responding. Please retry."),
        )

    @app.exception_handler(GitHubError)
    async def handle_remaining(_: Request, exc: GitHubError) -> JSONResponse:
        """Catch-all so a new GitHub exception can never leak a 500 with a traceback."""
        logger.error("github.unexpected_failure", detail=str(exc), kind=type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=_body("github_error", "Unexpected response from GitHub."),
        )
