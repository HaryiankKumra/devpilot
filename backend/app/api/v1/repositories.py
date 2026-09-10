"""Repository and pull request endpoints.

Every route here is scoped to the authenticated user. Ownership is checked in
the service layer, which answers 404 rather than 403 for someone else's
repository -- confirming a resource exists is itself information.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession, GitHub
from app.core.enums import PullRequestState
from app.db.repositories.repository import PullRequestStore
from app.schemas.repository import (
    IndexResponse,
    InstallationRead,
    PullRequestRead,
    RepositoryRead,
    SyncRequest,
    SyncResponse,
)
from app.services import repositories as repository_service
from app.services.dispatch import dispatch_repository_index

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get("", response_model=list[RepositoryRead], summary="List tracked repositories")
def list_repositories(user: CurrentUser, session: DbSession) -> list[RepositoryRead]:
    """Every repository connected to the signed-in account."""
    found = repository_service.list_repositories(session, owner=user)
    return [RepositoryRead.model_validate(repository) for repository in found]


@router.get(
    "/installations",
    response_model=list[InstallationRead],
    summary="List GitHub App installations",
)
def list_installations(user: CurrentUser, client: GitHub) -> list[InstallationRead]:
    """Installations of the DevPilot App that this deployment can see.

    Used by the UI to offer a choice of installation to sync from. Declared
    before `/{repository_id}` so the literal path is not captured as an id.
    """
    return [
        InstallationRead(
            id=installation.id,
            account_login=installation.account.login if installation.account else None,
            repository_selection=installation.repository_selection,
        )
        for installation in client.list_installations()
    ]


@router.post(
    "/sync",
    response_model=SyncResponse,
    status_code=status.HTTP_200_OK,
    summary="Reconcile repositories with a GitHub App installation",
)
def sync_repositories(
    payload: SyncRequest,
    user: CurrentUser,
    session: DbSession,
    client: GitHub,
) -> SyncResponse:
    """Pull the installation's repository list and upsert it locally.

    Repositories the installation no longer exposes are deactivated rather than
    deleted, so their review history survives an uninstall.
    """
    result = repository_service.sync_installation_repositories(
        session, owner=user, client=client, installation_id=payload.installation_id
    )
    session.commit()
    return SyncResponse(
        created=result.created, updated=result.updated, deactivated=result.deactivated
    )


@router.get(
    "/{repository_id}",
    response_model=RepositoryRead,
    summary="One repository",
    responses={404: {"description": "No such repository for this user."}},
)
def get_repository(
    repository_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> RepositoryRead:
    repository = repository_service.get_owned_repository(
        session, owner=user, repository_id=repository_id
    )
    return RepositoryRead.model_validate(repository)


@router.get(
    "/{repository_id}/pull-requests",
    response_model=list[PullRequestRead],
    summary="Pull requests in a repository",
    responses={404: {"description": "No such repository for this user."}},
)
def list_pull_requests(
    repository_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
    state: PullRequestState | None = None,
) -> list[PullRequestRead]:
    """Pull requests DevPilot has recorded, newest first."""
    repository = repository_service.get_owned_repository(
        session, owner=user, repository_id=repository_id
    )
    found = PullRequestStore(session).list_for_repository(repository.id, state=state)
    return [PullRequestRead.model_validate(pull_request) for pull_request in found]


@router.post(
    "/{repository_id}/index",
    response_model=IndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Index a repository for semantic search",
    responses={404: {"description": "No such repository for this user."}},
)
def index_repository(
    repository_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> IndexResponse:
    """Queue an indexing run.

    Answers 202 rather than doing the work: indexing walks the whole repository,
    fetches every source file and calls an embedding API, which is far past what
    an HTTP request should hold open.
    """
    repository = repository_service.get_owned_repository(
        session, owner=user, repository_id=repository_id
    )

    celery_task_id = dispatch_repository_index(repository.id)

    return IndexResponse(
        repository_id=repository.id,
        queued=celery_task_id is not None,
        celery_task_id=celery_task_id,
        detail=(
            "Indexing queued."
            if celery_task_id
            else "Could not reach the task queue; indexing was not started."
        ),
    )
