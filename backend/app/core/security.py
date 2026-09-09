"""Password hashing and access-token handling.

This module is deliberately free of database and HTTP concerns: it turns
passwords into digests and user ids into signed tokens, and nothing else. That
makes each rule here testable in isolation, which matters more for security code
than for anything else in the project.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError, VerifyMismatchError
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Argon2id with the library's current defaults, which track the OWASP guidance.
# Argon2 is memory-hard, so an attacker with GPUs gains far less over a
# defender than they would against a purely compute-bound hash.
_password_hasher = PasswordHasher()

# Argon2 has no short input limit the way bcrypt does (bcrypt silently truncates
# at 72 bytes), but an unbounded password is a denial-of-service vector: hashing
# is intentionally expensive, so a megabyte-long password would burn CPU on
# every login attempt. The API schema enforces this; it is defined here next to
# the hashing code it protects.
PASSWORD_MIN_LENGTH: Final = 12
PASSWORD_MAX_LENGTH: Final = 128

# Distinguishes an access token from any other token type added later (a refresh
# or email-verification token). Without it, a token minted for one purpose could
# be replayed for another.
ACCESS_TOKEN_TYPE: Final = "access"


class TokenPayload(BaseModel):
    """The validated claims of an access token."""

    subject: uuid.UUID = Field(description="The id of the authenticated user.")
    expires_at: datetime
    issued_at: datetime
    token_id: str = Field(description="Unique id for this token, for future revocation.")


class InvalidTokenError(Exception):
    """A token was missing, malformed, expired, or not signed by us."""


# --- Passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return an Argon2id digest of `password`.

    The returned string embeds the algorithm, version, cost parameters and salt
    alongside the digest, so verification needs nothing else stored.
    """
    return _password_hasher.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Check `password` against a stored digest.

    Returns `False` for every failure rather than raising, so a caller cannot
    accidentally distinguish "wrong password" from "corrupt hash" and turn that
    into an information leak.
    """
    try:
        return _password_hasher.verify(hashed_password, password)
    except VerifyMismatchError:
        return False
    except InvalidHashError:
        # A stored value that is not a valid Argon2 hash means corrupt data or a
        # botched migration, not a wrong password. Worth logging; still a failure.
        logger.warning("password.invalid_stored_hash")
        return False
    except Argon2Error:
        logger.exception("password.verification_error")
        return False


def needs_rehash(hashed_password: str) -> bool:
    """True when a stored digest uses weaker parameters than the current policy.

    Lets cost parameters be raised over time: the next successful login
    transparently upgrades the stored hash instead of leaving old accounts on
    the old settings forever.
    """
    try:
        return _password_hasher.check_needs_rehash(hashed_password)
    except InvalidHashError:
        return True


# --- Access tokens -----------------------------------------------------------


def create_access_token(
    subject: uuid.UUID,
    settings: Settings,
    expires_delta: timedelta | None = None,
) -> str:
    """Mint a signed access token for `subject`.

    `expires_delta` is injectable so tests can produce an already-expired token
    without manipulating the clock.
    """
    now = datetime.now(UTC)
    expires_at = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))

    claims = {
        "sub": str(subject),
        "exp": expires_at,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "type": ACCESS_TOKEN_TYPE,
    }
    return jwt.encode(
        claims,
        settings.secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str, settings: Settings) -> TokenPayload:
    """Verify a token and return its claims, or raise `InvalidTokenError`.

    Every failure mode collapses into one exception type: the caller must not be
    able to tell an expired token from a forged one, because that difference is
    useful to an attacker and to nobody else.
    """
    try:
        claims = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            # Pinning the accepted algorithm is what prevents algorithm-confusion
            # attacks, where a token is re-signed with `none` or with a symmetric
            # algorithm using a public key as the secret.
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError("Token is invalid.") from exc

    if claims.get("type") != ACCESS_TOKEN_TYPE:
        raise InvalidTokenError("Token is not an access token.")

    try:
        subject = uuid.UUID(claims["sub"])
    except (ValueError, TypeError) as exc:
        raise InvalidTokenError("Token subject is not a valid user id.") from exc

    return TokenPayload(
        subject=subject,
        expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
        issued_at=datetime.fromtimestamp(claims["iat"], tz=UTC),
        token_id=claims["jti"],
    )
