"""Tests for linking a DevPilot account to a GitHub identity.

The `state` parameter carries most of the weight here. Without it the callback
is a login-CSRF hole: an attacker sends a victim a callback URL carrying the
attacker's OAuth code, and the attacker's GitHub identity gets attached to the
victim's account.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import GitHubMode, Settings
from app.db.models.user import User
from app.services import github_link
from app.services.auth import register_user
from app.services.github_link import InvalidOAuthStateError
from tests.conftest import TEST_PASSWORD

STATUS = "/api/v1/github/status"
AUTHORIZE = "/api/v1/github/authorize"
CALLBACK = "/api/v1/github/callback"
LINK = "/api/v1/github/link"


@pytest.fixture
def oauth_settings(settings: Settings) -> Settings:
    """Live-mode OAuth settings. Mock mode never sends the browser to GitHub."""
    return settings.model_copy(
        update={
            "github_mode": GitHubMode.LIVE,
            "github_client_id": "Iv1.example",
            "github_client_secret": SecretStr("client-secret"),
            "github_app_slug": "devpilot",
        }
    )


class TestState:
    def test_round_trips_the_user_id(self, registered_user: User, settings: Settings) -> None:
        state = github_link._encode_state(registered_user, settings)

        assert github_link.decode_state(state, settings) == registered_user.id

    def test_rejects_a_tampered_state(self, registered_user: User, settings: Settings) -> None:
        """This is the whole point: a state we did not sign must not be accepted."""
        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
                "type": "github_oauth_state",
            },
            "an-attacker-chosen-key",
            algorithm="HS256",
        )

        with pytest.raises(InvalidOAuthStateError):
            github_link.decode_state(forged, settings)

    def test_rejects_an_expired_state(self, registered_user: User, settings: Settings) -> None:
        stale = jwt.encode(
            {
                "sub": str(registered_user.id),
                "exp": int((datetime.now(UTC) - timedelta(seconds=1)).timestamp()),
                "type": "github_oauth_state",
            },
            settings.secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )

        with pytest.raises(InvalidOAuthStateError):
            github_link.decode_state(stale, settings)

    def test_rejects_an_access_token_used_as_a_state(
        self, registered_user: User, settings: Settings
    ) -> None:
        """Tokens minted for one purpose must not be replayable for another."""
        from app.core.security import create_access_token

        access_token = create_access_token(registered_user.id, settings)

        with pytest.raises(InvalidOAuthStateError):
            github_link.decode_state(access_token, settings)

    def test_rejects_arbitrary_text(self, settings: Settings) -> None:
        with pytest.raises(InvalidOAuthStateError):
            github_link.decode_state("not-a-token", settings)


class TestAuthorizeUrl:
    def test_mock_mode_points_back_at_our_own_callback(
        self, registered_user: User, settings: Settings
    ) -> None:
        """No GitHub to visit in mock mode, so the round trip completes locally
        -- through the real callback, so the state check is still exercised."""
        url = github_link.build_authorize_url(registered_user, settings)

        assert url.startswith(settings.github_oauth_redirect_uri)
        assert f"code={github_link.MOCK_OAUTH_CODE}" in url
        assert "state=" in url

    def test_points_at_github_with_a_state(
        self, registered_user: User, oauth_settings: Settings
    ) -> None:
        url = github_link.build_authorize_url(registered_user, oauth_settings)

        assert url.startswith("https://github.com/login/oauth/authorize?")
        assert "client_id=Iv1.example" in url
        assert "state=" in url

    def test_requests_only_identity_scope(
        self, registered_user: User, oauth_settings: Settings
    ) -> None:
        """Repository access comes from the App installation, not this token."""
        url = github_link.build_authorize_url(registered_user, oauth_settings)

        assert "scope=read%3Auser" in url
        assert "repo" not in url.split("scope=")[1].split("&")[0]


class TestAuthorizeEndpoint:
    def test_returns_the_url_as_json_not_a_redirect(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """A redirect would 401 on every real click: the route needs the Bearer
        token, and a browser navigation cannot carry one."""
        response = client.get(AUTHORIZE, headers=auth_headers, follow_redirects=False)

        assert response.status_code == 200
        assert "authorize_url" in response.json()
        assert "state=" in response.json()["authorize_url"]

    def test_requires_authentication(self, client: TestClient) -> None:
        assert client.get(AUTHORIZE).status_code == 401

    def test_the_mock_round_trip_links_the_account(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Follow the URL the endpoint hands back, exactly as a browser would."""
        url = client.get(AUTHORIZE, headers=auth_headers).json()["authorize_url"]

        done = client.get(url, follow_redirects=False)

        assert done.status_code == 307
        assert "github=linked" in done.headers["location"]
        status = client.get(STATUS, headers=auth_headers).json()
        assert status["linked"] is True
        assert status["github_login"] == "devpilot-demo"


class TestStatusEndpoint:
    def test_reports_an_unlinked_account(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(STATUS, headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["linked"] is False
        assert response.json()["github_login"] is None

    def test_requires_authentication(self, client: TestClient) -> None:
        assert client.get(STATUS).status_code == 401

    def test_omits_the_install_url_when_no_app_slug_is_configured(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """The UI hides the button rather than offering a link that leads nowhere."""
        assert client.get(STATUS, headers=auth_headers).json()["install_url"] is None


class TestCompleteLink:
    def test_attaches_the_github_identity(self, db_session: Session, registered_user: User) -> None:
        from app.integrations.github.mock import MOCK_ACCOUNT, MockGitHubClient

        github_link.complete_link(
            db_session, user=registered_user, code="any", client=MockGitHubClient()
        )
        db_session.commit()

        assert registered_user.has_linked_github
        # The mock installation's owner -- so the linked user can then sync it.
        assert registered_user.github_login == MOCK_ACCOUNT.login
        assert registered_user.github_id == MOCK_ACCOUNT.id

    def test_refuses_an_identity_already_linked_elsewhere(
        self, db_session: Session, registered_user: User
    ) -> None:
        """One GitHub identity per account, or repository ownership is ambiguous."""
        from app.core.exceptions import ConflictError
        from app.integrations.github.mock import MockGitHubClient

        github_link.complete_link(
            db_session, user=registered_user, code="any", client=MockGitHubClient()
        )
        db_session.commit()

        other = register_user(db_session, email="second@example.com", password=TEST_PASSWORD)
        db_session.commit()

        with pytest.raises(ConflictError):
            github_link.complete_link(db_session, user=other, code="any", client=MockGitHubClient())

    def test_relinking_the_same_identity_is_allowed(
        self, db_session: Session, registered_user: User
    ) -> None:
        from app.integrations.github.mock import MockGitHubClient

        for _ in range(2):
            github_link.complete_link(
                db_session, user=registered_user, code="any", client=MockGitHubClient()
            )
            db_session.commit()

        assert registered_user.has_linked_github


class TestCallbackEndpoint:
    def test_rejects_a_callback_with_an_invalid_state(self, client: TestClient) -> None:
        response = client.get(
            CALLBACK, params={"code": "x", "state": "forged"}, follow_redirects=False
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_oauth_state"

    def test_requires_both_code_and_state(self, client: TestClient) -> None:
        assert client.get(CALLBACK, params={"code": "x"}).status_code == 422
        assert client.get(CALLBACK, params={"state": "y"}).status_code == 422

    def test_links_and_redirects_to_the_frontend(
        self, client: TestClient, settings: Settings, registered_user: User
    ) -> None:
        state = github_link._encode_state(registered_user, settings)

        response = client.get(
            CALLBACK, params={"code": "the-code", "state": state}, follow_redirects=False
        )

        assert response.status_code == 307
        assert response.headers["location"].startswith(settings.frontend_base_url)
        assert "github=linked" in response.headers["location"]


class TestUnlink:
    def test_detaches_without_deleting_the_account(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        registered_user: User,
    ) -> None:
        from app.integrations.github.mock import MockGitHubClient

        github_link.complete_link(
            db_session, user=registered_user, code="any", client=MockGitHubClient()
        )
        db_session.commit()

        response = client.delete(LINK, headers=auth_headers)

        assert response.status_code == 200
        assert registered_user.github_id is None
        assert registered_user.github_login is None
        # The account itself survives.
        assert client.get("/api/v1/auth/me", headers=auth_headers).status_code == 200

    def test_requires_authentication(self, client: TestClient) -> None:
        assert client.delete(LINK).status_code == 401
