"""Tests for `installation` and `installation_repositories` deliveries.

These are how a repository becomes tracked without anyone pressing Sync: the
user installs the App on GitHub, and the resulting webhook tells DevPilot which
repositories it now has access to.

The interesting problem is ownership. The delivery carries no DevPilot session,
so the only link back to a local account is the *sender's* GitHub id, recorded
when that user linked their account.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.repository import Repository
from app.db.models.user import User
from app.integrations.github.webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    compute_signature,
)
from app.main import create_app

WEBHOOK_URL = "/api/v1/webhooks/github"
WEBHOOK_SECRET = "test-webhook-secret"
INSTALLATION_ID = 10_000_001
LINKED_GITHUB_ID = 5_150_001


@pytest.fixture
def app(settings: Settings, db_session: Session) -> FastAPI:
    from collections.abc import Iterator

    from app.api.deps import get_db

    application = create_app(
        settings.model_copy(update={"github_webhook_secret": SecretStr(WEBHOOK_SECRET)})
    )

    def override_get_db() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    return application


@pytest.fixture
def linked_user(db_session: Session, registered_user: User) -> User:
    """A DevPilot account with a GitHub identity attached."""
    registered_user.github_id = LINKED_GITHUB_ID
    registered_user.github_login = "installer"
    db_session.commit()
    return registered_user


def post(
    client: TestClient,
    payload: dict[str, Any],
    *,
    event: str = "installation",
) -> Any:
    body = json.dumps(payload).encode()
    return client.post(
        WEBHOOK_URL,
        content=body,
        headers={
            EVENT_HEADER: event,
            DELIVERY_HEADER: str(uuid.uuid4()),
            SIGNATURE_HEADER: compute_signature(body, WEBHOOK_SECRET),
            "Content-Type": "application/json",
        },
    )


def repository_entry(repo_id: int, name: str) -> dict[str, Any]:
    return {
        "id": repo_id,
        "name": name,
        "full_name": f"devpilot-demo/{name}",
        "private": False,
    }


def installation_payload(
    *,
    action: str = "created",
    sender_id: int = LINKED_GITHUB_ID,
    repositories: list[dict[str, Any]] | None = None,
    added: list[dict[str, Any]] | None = None,
    removed: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "installation": {
            "id": INSTALLATION_ID,
            "account": {"id": 4_242_001, "login": "devpilot-demo"},
            "repository_selection": "selected",
        },
        "sender": {"id": sender_id, "login": "installer"},
    }
    if repositories is not None:
        payload["repositories"] = repositories
    if added is not None:
        payload["repositories_added"] = added
    if removed is not None:
        payload["repositories_removed"] = removed
    return payload


class TestInstallationCreated:
    def test_tracks_the_repositories_it_was_installed_on(
        self, client: TestClient, db_session: Session, linked_user: User
    ) -> None:
        response = post(
            client,
            installation_payload(
                repositories=[repository_entry(1, "alpha"), repository_entry(2, "beta")]
            ),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "processed"
        stored = db_session.execute(select(Repository)).scalars().all()
        assert {r.full_name for r in stored} == {
            "devpilot-demo/alpha",
            "devpilot-demo/beta",
        }

    def test_assigns_ownership_to_the_installing_user(
        self, client: TestClient, db_session: Session, linked_user: User
    ) -> None:
        """The sender's GitHub id is the only link back to a local account."""
        post(client, installation_payload(repositories=[repository_entry(1, "alpha")]))

        stored = db_session.execute(select(Repository)).scalar_one()
        assert stored.owner_id == linked_user.id

    def test_records_the_installation_id_for_later_token_minting(
        self, client: TestClient, db_session: Session, linked_user: User
    ) -> None:
        post(client, installation_payload(repositories=[repository_entry(1, "alpha")]))

        stored = db_session.execute(select(Repository)).scalar_one()
        assert stored.installation_id == INSTALLATION_ID

    def test_is_idempotent_across_redelivery(
        self, client: TestClient, db_session: Session, linked_user: User
    ) -> None:
        payload = installation_payload(repositories=[repository_entry(1, "alpha")])

        post(client, payload)
        post(client, payload)  # a fresh delivery id, same content

        stored = db_session.execute(select(Repository)).scalars().all()
        assert len(stored) == 1


class TestUnclaimedInstallation:
    def test_is_ignored_when_no_local_account_is_linked(
        self, client: TestClient, db_session: Session, registered_user: User
    ) -> None:
        """Nobody has linked that GitHub identity, so there is no owner to give
        the repositories to. Recorded, not treated as an error: the user may
        link afterwards and sync manually."""
        response = post(
            client,
            installation_payload(sender_id=999_999, repositories=[repository_entry(1, "alpha")]),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert db_session.execute(select(Repository)).first() is None

    def test_falls_back_to_the_installation_account(
        self, client: TestClient, db_session: Session, registered_user: User
    ) -> None:
        """Covers a personal installation, where the sender and the account
        holder are the same person."""
        registered_user.github_id = 4_242_001  # the installation's account id
        db_session.commit()

        response = post(
            client,
            installation_payload(sender_id=999_999, repositories=[repository_entry(1, "alpha")]),
        )

        assert response.json()["status"] == "processed"
        assert db_session.execute(select(Repository)).scalar_one().owner_id == registered_user.id


class TestInstallationRemoved:
    @pytest.mark.parametrize("action", ["deleted", "suspend"])
    def test_deactivates_rather_than_deletes(
        self, client: TestClient, db_session: Session, linked_user: User, action: str
    ) -> None:
        """Deleting would cascade to reviews and findings, discarding history
        that is still worth reading after an uninstall."""
        post(client, installation_payload(repositories=[repository_entry(1, "alpha")]))

        response = post(client, installation_payload(action=action))

        assert response.json()["status"] == "processed"
        stored = db_session.execute(select(Repository)).scalar_one()
        assert stored.is_active is False
        assert stored.full_name == "devpilot-demo/alpha"

    def test_reinstalling_reactivates_the_same_rows(
        self, client: TestClient, db_session: Session, linked_user: User
    ) -> None:
        post(client, installation_payload(repositories=[repository_entry(1, "alpha")]))
        post(client, installation_payload(action="deleted"))

        post(
            client,
            installation_payload(action="created", repositories=[repository_entry(1, "alpha")]),
        )

        stored = db_session.execute(select(Repository)).scalars().all()
        assert len(stored) == 1
        assert stored[0].is_active is True


class TestInstallationRepositoriesChanged:
    def test_adds_newly_granted_repositories(
        self, client: TestClient, db_session: Session, linked_user: User
    ) -> None:
        response = post(
            client,
            installation_payload(action="added", added=[repository_entry(3, "gamma")]),
            event="installation_repositories",
        )

        assert response.json()["status"] == "processed"
        stored = db_session.execute(select(Repository)).scalar_one()
        assert stored.full_name == "devpilot-demo/gamma"

    def test_deactivates_revoked_repositories(
        self, client: TestClient, db_session: Session, linked_user: User
    ) -> None:
        post(
            client,
            installation_payload(action="added", added=[repository_entry(3, "gamma")]),
            event="installation_repositories",
        )

        post(
            client,
            installation_payload(action="removed", removed=[repository_entry(3, "gamma")]),
            event="installation_repositories",
        )

        stored = db_session.execute(select(Repository)).scalar_one()
        assert stored.is_active is False
