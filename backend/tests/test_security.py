"""Tests for password hashing and access tokens.

Security code earns closer testing than the rest of the project: its failures
are silent, and "it works" is indistinguishable from "it accepts everything"
without tests that assert the negative cases.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.security import (
    ACCESS_TOKEN_TYPE,
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

PASSWORD = "correct-horse-battery-staple"


class TestPasswordHashing:
    def test_hashes_are_verifiable(self) -> None:
        digest = hash_password(PASSWORD)

        assert verify_password(PASSWORD, digest)

    def test_rejects_the_wrong_password(self) -> None:
        assert not verify_password("not-the-password", hash_password(PASSWORD))

    def test_never_stores_the_plaintext(self) -> None:
        digest = hash_password(PASSWORD)

        assert PASSWORD not in digest

    def test_uses_argon2id(self) -> None:
        """Argon2id is memory-hard, which blunts GPU-accelerated cracking."""
        assert hash_password(PASSWORD).startswith("$argon2id$")

    def test_salts_every_hash_independently(self) -> None:
        """Equal passwords must not produce equal digests, or a leaked table
        would reveal which accounts share a password."""
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    def test_treats_a_corrupt_stored_hash_as_a_failure(self) -> None:
        """A malformed digest must not raise into the request path."""
        assert not verify_password(PASSWORD, "not-a-valid-argon2-hash")

    def test_is_case_sensitive(self) -> None:
        assert not verify_password(PASSWORD.upper(), hash_password(PASSWORD))


class TestAccessTokens:
    def test_round_trips_the_subject(self, settings: Settings) -> None:
        user_id = uuid.uuid4()

        payload = decode_access_token(create_access_token(user_id, settings), settings)

        assert payload.subject == user_id

    def test_sets_an_expiry_from_settings(self, settings: Settings) -> None:
        payload = decode_access_token(create_access_token(uuid.uuid4(), settings), settings)

        expected = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
        # Allow a few seconds for the time spent inside the test itself.
        assert abs((payload.expires_at - expected).total_seconds()) < 5

    def test_rejects_an_expired_token(self, settings: Settings) -> None:
        expired = create_access_token(uuid.uuid4(), settings, timedelta(seconds=-1))

        with pytest.raises(InvalidTokenError):
            decode_access_token(expired, settings)

    def test_rejects_a_token_signed_with_another_key(self, settings: Settings) -> None:
        token = create_access_token(uuid.uuid4(), settings)
        other = Settings(secret_key=SecretStr("a-different-secret-key-entirely-x"))

        with pytest.raises(InvalidTokenError):
            decode_access_token(token, other)

    def test_rejects_an_unsigned_token(self, settings: Settings) -> None:
        """The `alg=none` attack: strip the signature and claim it is valid.

        Pinning the accepted algorithm on decode is what stops this.
        """
        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "exp": datetime.now(UTC) + timedelta(hours=1),
                "iat": datetime.now(UTC),
                "jti": "forged",
                "type": ACCESS_TOKEN_TYPE,
            },
            key="",
            algorithm="none",
        )

        with pytest.raises(InvalidTokenError):
            decode_access_token(forged, settings)

    def test_rejects_a_token_of_another_type(self, settings: Settings) -> None:
        """Stops a token minted for one purpose being replayed for another."""
        other_type = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "exp": datetime.now(UTC) + timedelta(hours=1),
                "iat": datetime.now(UTC),
                "jti": str(uuid.uuid4()),
                "type": "refresh",
            },
            settings.secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )

        with pytest.raises(InvalidTokenError):
            decode_access_token(other_type, settings)

    def test_rejects_a_token_without_an_expiry(self, settings: Settings) -> None:
        """A token that never expires cannot be aged out after a leak."""
        no_expiry = jwt.encode(
            {"sub": str(uuid.uuid4()), "iat": datetime.now(UTC), "jti": "x", "type": "access"},
            settings.secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )

        with pytest.raises(InvalidTokenError):
            decode_access_token(no_expiry, settings)

    def test_rejects_a_non_uuid_subject(self, settings: Settings) -> None:
        malformed = jwt.encode(
            {
                "sub": "not-a-uuid",
                "exp": datetime.now(UTC) + timedelta(hours=1),
                "iat": datetime.now(UTC),
                "jti": str(uuid.uuid4()),
                "type": ACCESS_TOKEN_TYPE,
            },
            settings.secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )

        with pytest.raises(InvalidTokenError):
            decode_access_token(malformed, settings)

    def test_rejects_arbitrary_strings(self, settings: Settings) -> None:
        with pytest.raises(InvalidTokenError):
            decode_access_token("neither.a.jwt", settings)

    def test_issues_a_unique_id_per_token(self, settings: Settings) -> None:
        """A per-token id is what a future revocation list would key on."""
        user_id = uuid.uuid4()

        first = decode_access_token(create_access_token(user_id, settings), settings)
        second = decode_access_token(create_access_token(user_id, settings), settings)

        assert first.token_id != second.token_id
