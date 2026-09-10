"""Repository management: syncing what GitHub shows us into our own tables.

The central operation is `sync_installation_repositories`, which is an **upsert
keyed on GitHub's repository id**, not on the name. Repositories get renamed and
transferred between organisations; keying on `full_name` would create a
duplicate row every time that happened and orphan the review history attached to
the old name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models.repository import Repository
from app.db.models.user import User
from app.db.repositories.repository import RepositoryStore
from app.integrations.github.client import GitHubClient
from app.integrations.github.models import GitHubInstallation, GitHubRepository

logger = get_logger(__name__)


@dataclass(frozen=True)
class SyncResult:
    """What one synchronisation changed."""

    created: int
    updated: int
    deactivated: int

    @property
    def total_seen(self) -> int:
        return self.created + self.updated


def upsert_repository(
    session: Session,
    *,
    owner: User,
    remote: GitHubRepository,
    installation_id: int | None,
) -> tuple[Repository, bool]:
    """Create or refresh the local row for one GitHub repository.

    Returns the row and whether it was newly created.
    """
    store = RepositoryStore(session)
    existing = store.get_by_github_id(remote.id, owner_id=owner.id)

    if existing is None:
        created = store.add(
            Repository(
                owner_id=owner.id,
                github_repo_id=remote.id,
                full_name=remote.full_name,
                default_branch=remote.default_branch,
                is_private=remote.private,
                installation_id=installation_id,
                is_active=True,
            )
        )
        logger.info("repository.created", repository_id=str(created.id), full_name=remote.full_name)
        return created, True

    # Refresh the mutable fields. `github_repo_id` and `owner_id` are identity
    # and are deliberately never rewritten here.
    existing.full_name = remote.full_name
    existing.default_branch = remote.default_branch
    existing.is_private = remote.private
    if installation_id is not None:
        existing.installation_id = installation_id
    # Re-appearing in a sync means the app is installed again.
    existing.is_active = True
    return existing, False


def installations_for(owner: User, client: GitHubClient) -> list[GitHubInstallation]:
    """The App installations belonging to this user's linked GitHub identity.

    `client.list_installations()` returns every installation of the *App*, which
    in a deployment with more than one user means everybody's. Filtering here is
    what stops one user seeing -- or syncing from -- another's installation.

    A user who has not linked a GitHub identity owns no installations, which is
    the honest answer: DevPilot has nothing to match against, so it cannot know
    that any installation is theirs.

    Matching on the account id rather than the login is deliberate: logins can be
    renamed and reused, numeric ids cannot.
    """
    if owner.github_id is None:
        return []

    return [
        installation
        for installation in client.list_installations()
        if installation.account is not None and installation.account.id == owner.github_id
    ]


def sync_installation_repositories(
    session: Session,
    *,
    owner: User,
    client: GitHubClient,
    installation_id: int,
) -> SyncResult:
    """Reconcile our repository rows with what the installation can see.

    Repositories that have disappeared are **deactivated, not deleted**. Deleting
    would cascade to their pull requests, reviews and findings, discarding
    history that is still worth reading -- and an app is often uninstalled and
    reinstalled, at which point the same rows come back.

    The installation id arrives in a request body, so it is checked against the
    caller's own installations before it is used. Without that check the endpoint
    is an authorization hole: DevPilot authenticates to GitHub as the *App*, not
    as the user, so it will cheerfully mint a token for any installation id it is
    handed and list somebody else's private repositories into the caller's
    account. Installation ids are sequential integers, so guessing them is not a
    meaningful obstacle.
    """
    if not any(
        installation.id == installation_id for installation in installations_for(owner, client)
    ):
        # 404 rather than 403: confirming that an installation exists is itself
        # information the caller has not earned.
        raise NotFoundError("No such installation for this account.")

    remote_repositories = client.list_installation_repositories(installation_id)

    created = 0
    updated = 0
    seen_github_ids: set[int] = set()

    for remote in remote_repositories:
        _, was_created = upsert_repository(
            session, owner=owner, remote=remote, installation_id=installation_id
        )
        seen_github_ids.add(remote.id)
        if was_created:
            created += 1
        else:
            updated += 1

    deactivated = _deactivate_missing(
        session, owner=owner, installation_id=installation_id, seen_github_ids=seen_github_ids
    )

    logger.info(
        "repository.sync_completed",
        installation_id=installation_id,
        created=created,
        updated=updated,
        deactivated=deactivated,
    )
    return SyncResult(created=created, updated=updated, deactivated=deactivated)


def _deactivate_missing(
    session: Session,
    *,
    owner: User,
    installation_id: int,
    seen_github_ids: set[int],
) -> int:
    """Mark repositories this installation no longer grants access to as inactive."""
    deactivated = 0
    for repository in RepositoryStore(session).list_for_owner(owner.id):
        if repository.installation_id != installation_id:
            continue
        if repository.github_repo_id in seen_github_ids:
            continue
        if repository.is_active:
            repository.is_active = False
            deactivated += 1
            logger.info(
                "repository.deactivated",
                repository_id=str(repository.id),
                full_name=repository.full_name,
            )
    return deactivated


def list_repositories(session: Session, *, owner: User) -> list[Repository]:
    """Every repository belonging to this user."""
    return list(RepositoryStore(session).list_for_owner(owner.id))


def get_owned_repository(session: Session, *, owner: User, repository_id: uuid.UUID) -> Repository:
    """Fetch one repository, enforcing that it belongs to `owner`.

    A repository owned by someone else answers 404 rather than 403. Confirming
    that a resource exists but is not yours still tells the caller it exists,
    which is information they should not have.
    """
    repository = RepositoryStore(session).get_by_id(repository_id)
    if repository is None or repository.owner_id != owner.id:
        raise NotFoundError("Repository not found.")
    return repository


def mark_indexed(repository: Repository, *, commit_sha: str) -> None:
    """Record that the repository has been embedded into `code_chunks`."""
    repository.indexed_at = datetime.now(UTC)
    repository.indexed_commit_sha = commit_sha
