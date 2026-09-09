"""Registration and authentication logic.

Imports no FastAPI: this module decides *what happened* (the email is taken, the
credentials are wrong) and raises domain exceptions. Choosing an HTTP status
code for each outcome is the route's job.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import ConflictError, DevPilotError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.db.models.user import User
from app.db.repositories.user import UserRepository

logger = get_logger(__name__)


class InvalidCredentialsError(DevPilotError):
    """Authentication failed.

    Deliberately does not distinguish "no such user" from "wrong password".
    Telling them apart lets an attacker enumerate which email addresses have
    accounts, which is useful for credential stuffing and phishing.
    """

    status_code = 401
    code = "invalid_credentials"


class InactiveUserError(DevPilotError):
    """The account exists and the password matched, but the account is disabled."""

    status_code = 403
    code = "inactive_user"


def register_user(
    session: Session,
    *,
    email: str,
    password: str,
    full_name: str | None = None,
) -> User:
    """Create an account and return it.

    Raises `ConflictError` when the email is already registered.
    """
    users = UserRepository(session)
    normalised_email = users.normalise_email(email)

    # Checked explicitly so the caller gets a clear 409 rather than an opaque
    # integrity error. The unique index remains the real guarantee: between this
    # check and the insert, a concurrent request could still win the race, and
    # the database is what actually settles it.
    if users.email_exists(normalised_email):
        raise ConflictError("An account with this email address already exists.")

    user = users.add(
        User(
            email=normalised_email,
            hashed_password=hash_password(password),
            full_name=full_name,
        )
    )
    logger.info("auth.user_registered", user_id=str(user.id))
    return user


def authenticate_user(session: Session, *, email: str, password: str) -> User:
    """Return the user matching these credentials.

    Raises `InvalidCredentialsError` if they do not match, or
    `InactiveUserError` if the account is disabled.
    """
    users = UserRepository(session)
    user = users.get_by_email(email)

    if user is None:
        # Hash the supplied password anyway. Skipping the hash for an unknown
        # address would make failed logins measurably faster for addresses that
        # do not exist, turning response time into an account-enumeration
        # oracle. Argon2 is slow by design, so the difference is easy to see.
        hash_password(password)
        logger.info("auth.login_failed", reason="unknown_email")
        raise InvalidCredentialsError("Incorrect email or password.")

    if not verify_password(password, user.hashed_password):
        logger.info("auth.login_failed", reason="bad_password", user_id=str(user.id))
        raise InvalidCredentialsError("Incorrect email or password.")

    if not user.is_active:
        logger.info("auth.login_denied", reason="inactive", user_id=str(user.id))
        raise InactiveUserError("This account has been disabled.")

    # A successful login is the only moment we hold the plaintext password, so
    # it is the only opportunity to upgrade a digest stored under weaker cost
    # parameters. Without this, raising the parameters would only ever protect
    # new accounts.
    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(password)
        logger.info("auth.password_rehashed", user_id=str(user.id))

    logger.info("auth.login_succeeded", user_id=str(user.id))
    return user


def issue_access_token(user: User, settings: Settings) -> tuple[str, int]:
    """Return an access token for `user` and its lifetime in seconds."""
    token = create_access_token(user.id, settings)
    return token, settings.access_token_expire_minutes * 60


def get_active_user(session: Session, user_id: uuid.UUID) -> User | None:
    """Return the user for an authenticated request, if they may still act.

    The account is re-checked on every request rather than trusted from the
    token, so disabling an account takes effect immediately instead of when the
    token happens to expire.
    """
    user = UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        return None
    return user
