"""Domain exceptions and the handlers that translate them into HTTP responses.

Service-layer code raises these plain Python exceptions and never imports
FastAPI. The mapping from a domain failure to a status code lives here, in one
place, so the HTTP contract stays consistent as the service layer grows.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class DevPilotError(Exception):
    """Base class for every expected, application-level failure."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DevPilotError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(DevPilotError):
    """The request cannot be applied to the current state of the resource."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class ValidationError(DevPilotError):
    """Input was well-formed but semantically invalid."""

    # Spelled numerically because Starlette renamed the constant for this
    # status code, and the pin allows both spellings of the dependency.
    status_code = 422
    code = "validation_error"


def _error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach DevPilot's exception handlers to the application."""

    @app.exception_handler(DevPilotError)
    async def handle_devpilot_error(_: Request, exc: DevPilotError) -> JSONResponse:
        logger.info("request.domain_error", code=exc.code, message=exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # The middleware has already logged the traceback with the request id.
        settings: Settings = request.app.state.settings
        # Leaking exception text to clients can disclose internals, so only do
        # it when debugging locally.
        message = str(exc) if settings.debug else "An unexpected error occurred."
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("internal_error", message),
        )
