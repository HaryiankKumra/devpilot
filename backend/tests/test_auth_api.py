"""End-to-end tests for the authentication endpoints."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import create_access_token
from app.db.models.user import User
from tests.conftest import TEST_PASSWORD

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"

VALID_REGISTRATION = {
    "email": "new.user@example.com",
    "password": "a-sufficiently-long-password",
    "full_name": "New User",
}


class TestRegistration:
    def test_creates_an_account(self, client: TestClient, db_session: Session) -> None:
        response = client.post(REGISTER, json=VALID_REGISTRATION)

        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "new.user@example.com"
        assert body["is_active"] is True
        assert uuid.UUID(body["id"])

        stored = db_session.execute(
            select(User).where(User.email == "new.user@example.com")
        ).scalar_one()
        assert stored.full_name == "New User"

    def test_never_returns_the_password_or_its_hash(self, client: TestClient) -> None:
        """The response schema has no such field, so this cannot regress quietly."""
        body = client.post(REGISTER, json=VALID_REGISTRATION).json()

        assert "password" not in body
        assert "hashed_password" not in body
        assert VALID_REGISTRATION["password"] not in str(body)

    def test_stores_the_password_hashed(self, client: TestClient, db_session: Session) -> None:
        client.post(REGISTER, json=VALID_REGISTRATION)

        stored = db_session.execute(select(User)).scalar_one()
        assert stored.hashed_password != VALID_REGISTRATION["password"]
        assert stored.hashed_password.startswith("$argon2id$")

    def test_rejects_a_duplicate_email(self, client: TestClient) -> None:
        client.post(REGISTER, json=VALID_REGISTRATION)

        response = client.post(REGISTER, json=VALID_REGISTRATION)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_treats_email_case_insensitively(self, client: TestClient) -> None:
        """`A@b.com` and `a@b.com` are the same mailbox and must be one account."""
        client.post(REGISTER, json=VALID_REGISTRATION)

        response = client.post(
            REGISTER, json={**VALID_REGISTRATION, "email": "New.User@EXAMPLE.com"}
        )

        assert response.status_code == 409

    def test_normalises_the_stored_email(self, client: TestClient, db_session: Session) -> None:
        client.post(REGISTER, json={**VALID_REGISTRATION, "email": "MiXeD@Example.COM"})

        stored = db_session.execute(select(User)).scalar_one()
        assert stored.email == "mixed@example.com"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("email", "not-an-email"),
            ("email", ""),
            ("password", "short"),
            ("password", "x" * 129),
        ],
    )
    def test_rejects_invalid_input(self, client: TestClient, field: str, value: str) -> None:
        response = client.post(REGISTER, json={**VALID_REGISTRATION, field: value})

        assert response.status_code == 422

    def test_does_not_return_a_token(self, client: TestClient) -> None:
        """Registration and login are separate steps by design."""
        body = client.post(REGISTER, json=VALID_REGISTRATION).json()

        assert "access_token" not in body


class TestLogin:
    def test_returns_a_bearer_token(self, client: TestClient, registered_user: User) -> None:
        response = client.post(
            LOGIN, json={"email": registered_user.email, "password": TEST_PASSWORD}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]
        assert body["expires_in"] > 0

    def test_accepts_a_differently_cased_email(
        self, client: TestClient, registered_user: User
    ) -> None:
        response = client.post(
            LOGIN, json={"email": registered_user.email.upper(), "password": TEST_PASSWORD}
        )

        assert response.status_code == 200

    def test_rejects_a_wrong_password(self, client: TestClient, registered_user: User) -> None:
        response = client.post(
            LOGIN, json={"email": registered_user.email, "password": "wrong-password"}
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"

    def test_rejects_an_unknown_email(self, client: TestClient) -> None:
        response = client.post(
            LOGIN, json={"email": "nobody@example.com", "password": TEST_PASSWORD}
        )

        assert response.status_code == 401

    def test_does_not_reveal_whether_an_account_exists(
        self, client: TestClient, registered_user: User
    ) -> None:
        """Distinguishable responses would let an attacker enumerate accounts."""
        unknown = client.post(
            LOGIN, json={"email": "nobody@example.com", "password": TEST_PASSWORD}
        )
        wrong_password = client.post(
            LOGIN, json={"email": registered_user.email, "password": "wrong-password"}
        )

        assert unknown.status_code == wrong_password.status_code
        assert unknown.json() == wrong_password.json()

    def test_rejects_a_disabled_account(
        self, client: TestClient, db_session: Session, registered_user: User
    ) -> None:
        registered_user.is_active = False
        db_session.commit()

        response = client.post(
            LOGIN, json={"email": registered_user.email, "password": TEST_PASSWORD}
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "inactive_user"


class TestCurrentUser:
    def test_returns_the_authenticated_account(
        self, client: TestClient, auth_headers: dict[str, str], registered_user: User
    ) -> None:
        response = client.get(ME, headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["email"] == registered_user.email

    def test_requires_a_token(self, client: TestClient) -> None:
        response = client.get(ME)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "not_authenticated"

    def test_advertises_the_bearer_scheme(self, client: TestClient) -> None:
        """RFC 6750: a 401 must tell the client how to authenticate."""
        response = client.get(ME)

        assert response.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.parametrize(
        "header",
        [
            "Bearer not-a-real-token",
            "Bearer ",
            "Basic dXNlcjpwYXNz",
            "not-even-a-scheme",
        ],
    )
    def test_rejects_malformed_credentials(self, client: TestClient, header: str) -> None:
        response = client.get(ME, headers={"Authorization": header})

        assert response.status_code == 401

    def test_rejects_an_expired_token(
        self, client: TestClient, settings: Settings, registered_user: User
    ) -> None:
        expired = create_access_token(registered_user.id, settings, timedelta(seconds=-1))

        response = client.get(ME, headers={"Authorization": f"Bearer {expired}"})

        assert response.status_code == 401

    def test_rejects_a_token_for_a_deleted_account(
        self, client: TestClient, settings: Settings
    ) -> None:
        """A validly signed token for a non-existent user must not authenticate."""
        token = create_access_token(uuid.uuid4(), settings)

        response = client.get(ME, headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401

    def test_rejects_a_token_once_the_account_is_disabled(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        registered_user: User,
    ) -> None:
        """Disabling an account takes effect immediately, not when the token expires.

        This is why the user is re-read from the database on every request
        instead of being trusted from the token's claims.
        """
        assert client.get(ME, headers=auth_headers).status_code == 200

        registered_user.is_active = False
        db_session.commit()

        assert client.get(ME, headers=auth_headers).status_code == 401
