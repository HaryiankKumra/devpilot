"""FastAPI dependency providers.

Routes depend on these rather than importing engines or clients directly, which
is what makes them trivially overridable in tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

import redis
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.redis import get_redis
from app.db.session import get_sessionmaker


def get_db() -> Iterator[Session]:
    """Yield a request-scoped database session.

    The session is closed when the request ends. Committing is the service
    layer's responsibility, so a handler that raises never half-commits.
    """
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def get_app_settings(request: Request) -> Settings:
    """Return the settings the running application was built with.

    Routes read configuration through this dependency rather than calling
    `get_settings()`, so a test can build an app with different settings and
    have the routes actually observe them.
    """
    settings: Settings = request.app.state.settings
    return settings


AppSettings = Annotated[Settings, Depends(get_app_settings)]
DbSession = Annotated[Session, Depends(get_db)]
RedisClient = Annotated["redis.Redis", Depends(get_redis)]
