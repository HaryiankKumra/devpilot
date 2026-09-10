"""Data access for the `repositories` and `pull_requests` tables.

As with `UserRepository`, nothing here commits: transaction boundaries belong to
the caller so several writes can succeed or fail together.

The name is unfortunate but unavoidable -- "repository" means both a GitHub
repository and the data-access pattern. `RepositoryRepository` would be worse.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import PullRequestState
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository


class RepositoryStore:
    """Queries and writes for GitHub repositories DevPilot tracks."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, repository_id: uuid.UUID) -> Repository | None:
        return self._session.get(Repository, repository_id)

    def get_by_github_id(self, github_repo_id: int, *, owner_id: uuid.UUID) -> Repository | None:
        """Look up one owner's copy of a repository.

        Scoped by owner because the same GitHub repository can be tracked by
        several DevPilot users, each with their own row and review history.
        """
        statement = select(Repository).where(
            Repository.github_repo_id == github_repo_id,
            Repository.owner_id == owner_id,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def get_by_installation_and_github_id(
        self, installation_id: int, github_repo_id: int
    ) -> Repository | None:
        """Find the row a webhook refers to.

        A delivery carries an installation, not a DevPilot user, and an
        installation belongs to exactly one owner -- so this pair identifies a
        single row even when several users track the same repository.
        """
        statement = select(Repository).where(
            Repository.github_repo_id == github_repo_id,
            Repository.installation_id == installation_id,
        )
        return self._session.execute(statement).scalars().first()

    def get_by_full_name(self, full_name: str) -> Repository | None:
        statement = select(Repository).where(Repository.full_name == full_name)
        return self._session.execute(statement).scalar_one_or_none()

    def list_for_owner(self, owner_id: uuid.UUID) -> Sequence[Repository]:
        statement = (
            select(Repository).where(Repository.owner_id == owner_id).order_by(Repository.full_name)
        )
        return self._session.execute(statement).scalars().all()

    def list_for_installation(self, installation_id: int) -> Sequence[Repository]:
        """Every repository under one GitHub App installation.

        Needed when an installation is deleted or suspended: the delivery names
        the installation, not the individual repositories.
        """
        statement = (
            select(Repository)
            .where(Repository.installation_id == installation_id)
            .order_by(Repository.full_name)
        )
        return self._session.execute(statement).scalars().all()

    def add(self, repository: Repository) -> Repository:
        self._session.add(repository)
        self._session.flush()
        return repository


class PullRequestStore:
    """Queries and writes for pull requests."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, pull_request_id: uuid.UUID) -> PullRequest | None:
        return self._session.get(PullRequest, pull_request_id)

    def get_by_number(self, repository_id: uuid.UUID, number: int) -> PullRequest | None:
        """Find by the natural key: a PR number is unique only within its repository."""
        statement = select(PullRequest).where(
            PullRequest.repository_id == repository_id,
            PullRequest.number == number,
        )
        return self._session.execute(statement).scalar_one_or_none()

    def list_for_repository(
        self, repository_id: uuid.UUID, *, state: PullRequestState | None = None
    ) -> Sequence[PullRequest]:
        statement = select(PullRequest).where(PullRequest.repository_id == repository_id)
        if state is not None:
            statement = statement.where(PullRequest.state == state)
        # Newest first: the most recent pull request is nearly always the one
        # someone opened the page to look at.
        statement = statement.order_by(PullRequest.number.desc())
        return self._session.execute(statement).scalars().all()

    def add(self, pull_request: PullRequest) -> PullRequest:
        self._session.add(pull_request)
        self._session.flush()
        return pull_request
