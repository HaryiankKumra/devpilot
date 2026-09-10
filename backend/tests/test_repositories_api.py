"""Tests for the repository endpoints and the sync logic behind them.

These run against the mock GitHub client, which is exactly how a developer with
no GitHub App registered runs the application.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models.repository import Repository
from app.db.models.user import User
from app.integrations.github.mock import MOCK_INSTALLATION_ID, MOCK_REPOSITORIES
from app.services import repositories as repository_service
from tests.conftest import TEST_PASSWORD

REPOSITORIES = "/api/v1/repositories"
SYNC = "/api/v1/repositories/sync"
INSTALLATIONS = "/api/v1/repositories/installations"


class TestAuthorisation:
    def test_every_repository_route_requires_a_token(self, client: TestClient) -> None:
        assert client.get(REPOSITORIES).status_code == 401
        assert client.get(INSTALLATIONS).status_code == 401
        assert client.post(SYNC, json={"installation_id": 1}).status_code == 401
        assert client.get(f"{REPOSITORIES}/{uuid.uuid4()}").status_code == 401


class TestListInstallations:
    def test_lists_what_the_client_can_see(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(INSTALLATIONS, headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert [item["id"] for item in body] == [MOCK_INSTALLATION_ID]
        assert body[0]["account_login"] == "devpilot-demo"


class TestSync:
    def test_creates_rows_for_every_repository(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            SYNC, json={"installation_id": MOCK_INSTALLATION_ID}, headers=auth_headers
        )

        assert response.status_code == 200
        assert response.json() == {
            "created": len(MOCK_REPOSITORIES),
            "updated": 0,
            "deactivated": 0,
        }

    def test_is_idempotent(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """Syncing twice must update, never duplicate."""
        payload = {"installation_id": MOCK_INSTALLATION_ID}
        client.post(SYNC, json=payload, headers=auth_headers)

        second = client.post(SYNC, json=payload, headers=auth_headers)

        assert second.json() == {
            "created": 0,
            "updated": len(MOCK_REPOSITORIES),
            "deactivated": 0,
        }
        listed = client.get(REPOSITORIES, headers=auth_headers).json()
        assert len(listed) == len(MOCK_REPOSITORIES)

    def test_stores_the_installation_id(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Needed later to mint a token scoped to this repository."""
        client.post(SYNC, json={"installation_id": MOCK_INSTALLATION_ID}, headers=auth_headers)

        listed = client.get(REPOSITORIES, headers=auth_headers).json()
        assert all(item["installation_id"] == MOCK_INSTALLATION_ID for item in listed)

    def test_reports_an_unknown_installation_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """A bad installation id must not surface as a 500 with a traceback."""
        response = client.post(SYNC, json={"installation_id": 999}, headers=auth_headers)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "github_not_found"


class TestSyncKeyedOnGitHubId:
    def test_a_renamed_repository_updates_rather_than_duplicates(
        self, db_session: Session, registered_user: User
    ) -> None:
        """Repositories get renamed and transferred. Keying on `full_name` would
        create a second row and orphan the review history on the old name."""
        remote = MOCK_REPOSITORIES[0]
        repository_service.upsert_repository(
            db_session, owner=registered_user, remote=remote, installation_id=1
        )
        db_session.commit()

        renamed = remote.model_copy(update={"full_name": "devpilot-demo/checkout"})
        row, created = repository_service.upsert_repository(
            db_session, owner=registered_user, remote=renamed, installation_id=1
        )
        db_session.commit()

        assert created is False
        assert row.full_name == "devpilot-demo/checkout"
        assert len(repository_service.list_repositories(db_session, owner=registered_user)) == 1


class TestDeactivation:
    def test_a_repository_no_longer_visible_is_deactivated_not_deleted(
        self, db_session: Session, registered_user: User
    ) -> None:
        """Deleting would cascade to reviews and findings, discarding history
        that is still worth reading after an uninstall."""
        vanished = MOCK_REPOSITORIES[0].model_copy(update={"id": 987_654})
        repository_service.upsert_repository(
            db_session,
            owner=registered_user,
            remote=vanished,
            installation_id=MOCK_INSTALLATION_ID,
        )
        db_session.commit()

        from app.integrations.github.mock import MockGitHubClient

        result = repository_service.sync_installation_repositories(
            db_session,
            owner=registered_user,
            client=MockGitHubClient(),
            installation_id=MOCK_INSTALLATION_ID,
        )
        db_session.commit()

        assert result.deactivated == 1
        still_present = repository_service.list_repositories(db_session, owner=registered_user)
        deactivated = [r for r in still_present if r.github_repo_id == 987_654]
        assert len(deactivated) == 1
        assert deactivated[0].is_active is False

    def test_reactivates_a_repository_that_comes_back(
        self, db_session: Session, registered_user: User
    ) -> None:
        """Apps are uninstalled and reinstalled; the same rows should return."""
        remote = MOCK_REPOSITORIES[0]
        row, _ = repository_service.upsert_repository(
            db_session, owner=registered_user, remote=remote, installation_id=1
        )
        row.is_active = False
        db_session.commit()

        refreshed, created = repository_service.upsert_repository(
            db_session, owner=registered_user, remote=remote, installation_id=1
        )

        assert created is False
        assert refreshed.is_active is True


class TestOwnershipIsolation:
    def _other_users_repository(self, db_session: Session) -> Repository:
        from app.services.auth import register_user

        other = register_user(db_session, email="someone.else@example.com", password=TEST_PASSWORD)
        row, _ = repository_service.upsert_repository(
            db_session, owner=other, remote=MOCK_REPOSITORIES[0], installation_id=1
        )
        db_session.commit()
        return row

    def test_another_users_repository_is_absent_from_the_list(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        self._other_users_repository(db_session)

        listed = client.get(REPOSITORIES, headers=auth_headers).json()

        assert listed == []

    def test_another_users_repository_answers_404_not_403(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        """403 would confirm the repository exists, which is itself information."""
        foreign = self._other_users_repository(db_session)

        response = client.get(f"{REPOSITORIES}/{foreign.id}", headers=auth_headers)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_another_users_pull_requests_are_not_reachable(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        foreign = self._other_users_repository(db_session)

        response = client.get(f"{REPOSITORIES}/{foreign.id}/pull-requests", headers=auth_headers)

        assert response.status_code == 404


class TestGetRepository:
    def test_returns_a_synced_repository(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        client.post(SYNC, json={"installation_id": MOCK_INSTALLATION_ID}, headers=auth_headers)
        listed = client.get(REPOSITORIES, headers=auth_headers).json()

        response = client.get(f"{REPOSITORIES}/{listed[0]['id']}", headers=auth_headers)

        assert response.status_code == 200
        assert response.json()["full_name"] == listed[0]["full_name"]

    def test_an_unknown_id_is_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(f"{REPOSITORIES}/{uuid.uuid4()}", headers=auth_headers)

        assert response.status_code == 404

    def test_a_malformed_id_is_rejected_as_invalid_input(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(f"{REPOSITORIES}/not-a-uuid", headers=auth_headers)

        assert response.status_code == 422
