"""FastAPI dependency providers.

Routes depend on these rather than importing engines or clients directly, which
is what makes them trivially overridable in tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

import redis
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import NotAuthenticatedError
from app.core.security import InvalidTokenError, decode_access_token
from app.db.models.user import User
from app.db.redis import get_redis
from app.db.session import get_sessionmaker
from app.services import auth as auth_service


def get_db() -> Iterator[Session]:
    """Yield a request-scoped database session.

    The session is closed when the request ends. Committing is the caller's
    responsibility, so a handler that raises never half-commits.
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


# `auto_error=False` so a missing header reaches our own handler and produces
# the standard `{"error": {...}}` envelope, rather than FastAPI's own 403 with a
# different shape. It also means every unauthenticated case answers 401.
_bearer_scheme = HTTPBearer(auto_error=False, description="A DevPilot access token.")

BearerToken = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


def get_current_user(
    credentials: BearerToken,
    session: DbSession,
    settings: AppSettings,
) -> User:
    """Resolve the caller's access token to an active user.

    Raises `NotAuthenticatedError` for every failure -- missing header, bad
    signature, expired token, deleted account, disabled account. They are
    deliberately indistinguishable to the client: telling them apart would let
    an attacker probe which user ids exist and whether a stolen token is merely
    expired or genuinely invalid.
    """
    if credentials is None:
        raise NotAuthenticatedError()

    try:
        payload = decode_access_token(credentials.credentials, settings)
    except InvalidTokenError as exc:
        raise NotAuthenticatedError() from exc

    # The account is re-read on every request rather than trusted from the
    # token's claims, so deactivating a user takes effect immediately instead of
    # whenever their current token happens to expire.
    user = auth_service.get_active_user(session, payload.subject)
    if user is None:
        raise NotAuthenticatedError()

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
