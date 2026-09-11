"""FastAPI application factory.

`create_app()` builds and wires the application rather than doing it at import
time, so tests can construct an app with overridden settings and dependencies.
The module-level `app` exists only as the ASGI entry point for uvicorn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_integration_exception_handlers
from app.api.middleware_rate_limit import RateLimitMiddleware
from app.api.middleware_security import SecurityHeadersMiddleware
from app.api.router import api_router
from app.api.v1 import health
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run startup and shutdown work around the served lifetime of the app."""
    settings: Settings = app.state.settings
    logger.info(
        "app.startup",
        environment=settings.environment.value,
        version=settings.version,
    )
    yield
    logger.info("app.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fully wired FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.project_name,
        version=settings.version,
        summary="AI-powered GitHub pull request review platform.",
        # Interactive docs describe every route and are safe locally, but they
        # also advertise the entire attack surface, so they are off in production.
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Starlette applies middleware bottom-up, so the last added runs first.
    # Rate limiting must run before anything expensive, and the request-id
    # context must be bound before rate limiting so a 429 is still traceable.
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)
    register_integration_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    # Last, because its catch-all route must lose to every real route above.
    # Only in the single-container deployment; Compose serves the bundle from
    # nginx and leaves this unset.
    if settings.static_dir is not None:
        from app.api.spa import mount_spa

        mount_spa(app, settings.static_dir)

    return app


app = create_app()
