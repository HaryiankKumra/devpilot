"""Request and response schemas for registration and login."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.security import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH


class RegisterRequest(BaseModel):
    """Payload for creating an account."""

    email: EmailStr = Field(description="Email address; stored lower-cased.")
    # Length is the only rule enforced. Composition requirements ("one digit,
    # one symbol") push people towards predictable substitutions such as
    # `Password1!` while blocking strong passphrases, so NIST SP 800-63B
    # recommends against them. The upper bound is a denial-of-service guard:
    # Argon2 hashing is deliberately expensive.
    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=f"At least {PASSWORD_MIN_LENGTH} characters.",
    )
    full_name: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    """Payload for exchanging credentials for an access token."""

    email: EmailStr
    # No length constraints: rejecting a wrong-length password before checking
    # it would reveal something about the stored password, and legacy accounts
    # may predate the current policy.
    password: str


class TokenResponse(BaseModel):
    """A freshly issued access token."""

    access_token: str
    # `bearer` is the scheme the client must use in the Authorization header.
    token_type: str = Field(default="bearer")
    expires_in: int = Field(description="Token lifetime in seconds.")


class UserRead(BaseModel):
    """A user as returned by the API.

    Note what is absent: `hashed_password` has no field here, so it cannot be
    serialised into a response even by accident.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    is_active: bool
    created_at: datetime
