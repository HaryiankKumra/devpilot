"""Linking a DevPilot account to a GitHub identity via OAuth.

The security-relevant part is the `state` parameter. Without it, an attacker can
send a victim a crafted callback URL carrying the *attacker's* OAuth code; the
victim's browser completes the flow while signed in as themselves, and the
attacker's GitHub identity ends up attached to the victim's account. That is
login CSRF, and the fix is to make the callback prove it belongs to a flow this
user actually started.

DevPilot signs the state as a short-lived JWT carrying the user's id. That keeps
the API stateless -- no server-side store of pending flows to expire or clean
up -- while still being unforgeable and impossible to replay after it expires.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import ConflictError, DevPilotError
from app.core.logging import get_logger
from app.db.models.user import User
from app.integrations.github.client import GitHubClient
from app.integrations.github.exceptions import GitHubConfigurationError

logger = get_logger(__name__)

# Long enough to sign in to GitHub and approve; short enough that a leaked link
# is not useful later.
OAUTH_STATE_LIFETIME = timedelta(minutes=10)
OAUTH_STATE_TOKEN_TYPE = "github_oauth_state"

# DevPilot only needs to know who the user is. Repository access comes from the
# App installation, not from this token, so no repository scope is requested.
OAUTH_SCOPES = "read:user"


class InvalidOAuthStateError(DevPilotError):
    """The callback did not come from a flow this user started."""

    status_code = 400
    code = "invalid_oauth_state"


MOCK_OAUTH_CODE = "mock-oauth-code"


def build_authorize_url(user: User, settings: Settings) -> str:
    """Return the URL to send the user to, carrying a signed state.

    In mock mode there is no GitHub to send them to, so the URL points straight
    back at our own callback with a placeholder code. The callback then runs
    exactly as it would for real -- state verified, code exchanged through the
    (mock) client, identity recorded -- which is what lets the full linking flow
    be driven from a browser with no credentials. Skipping the callback and
    writing the identity directly would leave the one security-relevant step,
    the state check, untested in the only place a browser exercises it.
    """
    if settings.github_is_mocked:
        query = urlencode({"code": MOCK_OAUTH_CODE, "state": _encode_state(user, settings)})
        return f"{settings.github_oauth_redirect_uri}?{query}"

    if not settings.github_client_id:
        raise GitHubConfigurationError(
            "GitHub OAuth is not configured. Set DEVPILOT_GITHUB_CLIENT_ID."
        )

    query = urlencode(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": settings.github_oauth_redirect_uri,
            "scope": OAUTH_SCOPES,
            "state": _encode_state(user, settings),
        }
    )
    return f"{settings.github_web_url}/login/oauth/authorize?{query}"


def _encode_state(user: User, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user.id),
            "iat": int(now.timestamp()),
            "exp": int((now + OAUTH_STATE_LIFETIME).timestamp()),
            "type": OAUTH_STATE_TOKEN_TYPE,
        },
        settings.secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_state(state: str, settings: Settings) -> uuid.UUID:
    """Return the user id a state belongs to, or raise `InvalidOAuthStateError`."""
    try:
        claims = jwt.decode(
            state,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        raise InvalidOAuthStateError("The GitHub sign-in link has expired or is invalid.") from exc

    # An access token must not be usable as a state, so the type is checked.
    if claims.get("type") != OAUTH_STATE_TOKEN_TYPE:
        raise InvalidOAuthStateError("The GitHub sign-in link is not valid.")

    try:
        return uuid.UUID(claims["sub"])
    except (ValueError, TypeError) as exc:
        raise InvalidOAuthStateError("The GitHub sign-in link is not valid.") from exc


def complete_link(
    session: Session,
    *,
    user: User,
    code: str,
    client: GitHubClient,
) -> User:
    """Exchange the OAuth code and attach the GitHub identity to `user`."""
    token = client.exchange_oauth_code(code)
    account = client.get_authenticated_user(token.access_token)

    # One GitHub identity per DevPilot account. Without this, two accounts could
    # both claim the same installation and repository ownership would be
    # ambiguous. The unique index is the real guarantee; this gives a clear 409.
    existing = session.execute(
        select(User).where(User.github_id == account.id, User.id != user.id)
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            "That GitHub account is already linked to a different DevPilot account."
        )

    user.github_id = account.id
    user.github_login = account.login
    logger.info("github.account_linked", user_id=str(user.id), github_login=account.login)
    return user


def unlink(user: User) -> User:
    """Detach the GitHub identity, leaving the DevPilot account intact."""
    logger.info("github.account_unlinked", user_id=str(user.id))
    user.github_id = None
    user.github_login = None
    return user


def build_installation_url(settings: Settings) -> str:
    """Where to send someone to install the App on their repositories.

    Uses the app slug when configured. This is a plain link rather than
    something DevPilot can do on the user's behalf: installing an app is a
    permission grant only its owner can make.
    """
    if not settings.github_app_slug:
        raise GitHubConfigurationError(
            "DEVPILOT_GITHUB_APP_SLUG is not set, so the install link cannot be built."
        )
    return f"{settings.github_web_url}/apps/{settings.github_app_slug}/installations/new"
