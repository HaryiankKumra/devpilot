"""Structured logging setup.

Every log line is a structured event, not a formatted string. Locally they are
rendered as colourised key/value pairs for readability; in CI and production
they are rendered as one JSON object per line so a log aggregator can index
fields such as `request_id` without regex parsing.

`request_id` is bound to a context variable by the request middleware, so any
log emitted while handling a request carries it automatically -- including logs
from deep inside service code that knows nothing about HTTP.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import Environment, Settings


def configure_logging(settings: Settings) -> None:
    """Configure structlog and route the stdlib logging module through it.

    Third-party libraries (uvicorn, SQLAlchemy) use stdlib logging, so we point
    the root logger at a structlog formatter to keep output in one format.
    """
    level = logging.getLevelNamesMapping()[settings.log_level.value]
    human_readable = settings.environment is Environment.LOCAL

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: Any = (
        structlog.dev.ConsoleRenderer() if human_readable else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Applied only to records coming from stdlib loggers.
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn installs its own handlers; drop them so lines are not duplicated.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(noisy).handlers = []
        logging.getLogger(noisy).propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structured logger."""
    return structlog.stdlib.get_logger(name)
