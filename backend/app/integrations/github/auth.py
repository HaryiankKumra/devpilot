"""GitHub App authentication.

A GitHub App never uses a long-lived API key. Instead there are two steps:

1. Sign a short-lived JWT with the App's RSA private key. This proves *which
   app* is calling, and can only list installations -- it cannot read code.
2. Exchange that JWT for an **installation access token**, scoped to one
   installation and valid for one hour. This is what can actually read a
   repository or post a comment.

The two-step design is the reason a leaked installation token is survivable: it
expires within the hour and only covers the repositories that installation was
granted. The private key is the thing that must never leak.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.github.exceptions import GitHubConfigurationError
from app.integrations.github.models import GitHubInstallationToken

logger = get_logger(__name__)

# GitHub rejects app JWTs valid for more than 10 minutes. Nine leaves room for
# clock skew between this host and GitHub without crossing the limit.
APP_JWT_LIFETIME_SECONDS = 9 * 60

# GitHub's clock may be slightly behind ours; backdating `iat` avoids a JWT
# being rejected as issued in the future. This is GitHub's own recommendation.
APP_JWT_CLOCK_SKEW_SECONDS = 60

# Refresh an installation token this long before it actually expires, so a
# request never starts with a token that dies mid-flight.
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)


def build_app_jwt(settings: Settings, *, now: datetime | None = None) -> str:
    """Return a signed JWT identifying the DevPilot GitHub App.

    Raises `GitHubConfigurationError` when the app id or private key is missing,
    which is the normal state before anyone has registered an app.
    """
    if not settings.github_app_id:
        raise GitHubConfigurationError(
            "DEVPILOT_GITHUB_APP_ID is not set. See docs/github-app-setup.md."
        )

    private_key = settings.resolve_github_private_key()
    if not private_key:
        raise GitHubConfigurationError(
            "No GitHub App private key configured. Set "
            "DEVPILOT_GITHUB_APP_PRIVATE_KEY or DEVPILOT_GITHUB_APP_PRIVATE_KEY_PATH."
        )

    issued_at = (now or datetime.now(UTC)) - timedelta(seconds=APP_JWT_CLOCK_SKEW_SECONDS)
    claims = {
        "iat": int(issued_at.timestamp()),
        "exp": int(issued_at.timestamp()) + APP_JWT_LIFETIME_SECONDS,
        "iss": settings.github_app_id,
    }

    try:
        return jwt.encode(claims, private_key, algorithm="RS256")
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        # An unreadable or wrong-type key is a configuration problem, not a
        # runtime fault, and must surface as one. PyJWT reports a malformed PEM
        # as `InvalidKeyError`, which is neither a ValueError nor a TypeError.
        raise GitHubConfigurationError(
            f"The GitHub App private key could not be used to sign a token: {exc}"
        ) from exc


@dataclass(frozen=True)
class CachedToken:
    token: str
    expires_at: datetime

    def is_usable(self, *, now: datetime) -> bool:
        return now + TOKEN_REFRESH_MARGIN < self.expires_at


class InstallationTokenCache:
    """Caches installation tokens until shortly before they expire.

    Without this, every GitHub call would spend a round trip minting a token
    that is valid for an hour. The lock matters because Celery runs several
    threads per worker, and two of them starting on the same repository would
    otherwise mint two tokens and race to store them.
    """

    def __init__(self) -> None:
        self._tokens: dict[int, CachedToken] = {}
        self._lock = threading.Lock()

    def get(self, installation_id: int, *, now: datetime | None = None) -> str | None:
        moment = now or datetime.now(UTC)
        with self._lock:
            cached = self._tokens.get(installation_id)
            if cached is not None and cached.is_usable(now=moment):
                return cached.token
            # Drop an expired entry so the map does not grow without bound.
            if cached is not None:
                del self._tokens[installation_id]
        return None

    def store(self, installation_id: int, token: GitHubInstallationToken) -> None:
        with self._lock:
            self._tokens[installation_id] = CachedToken(
                token=token.token, expires_at=token.expires_at
            )

    def invalidate(self, installation_id: int) -> None:
        """Forget a token GitHub has rejected, so the next call mints a fresh one."""
        with self._lock:
            self._tokens.pop(installation_id, None)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()


def seconds_until(moment: datetime) -> float:
    """Seconds from now until `moment`, never negative."""
    return max(0.0, moment.timestamp() - time.time())
