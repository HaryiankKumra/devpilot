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
from app.integrations.github.models import GitHubRepository

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
    existing = store.get_by_github_id(remote.id)

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
    """
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
