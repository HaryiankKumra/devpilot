"""Request and response schemas for repositories and pull requests."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import PullRequestState


class RepositoryRead(BaseModel):
    """A tracked repository."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    github_repo_id: int
    full_name: str
    default_branch: str
    is_private: bool
    is_active: bool = Field(
        description="False once the GitHub App is uninstalled; history is kept."
    )
    installation_id: int | None
    indexed_at: datetime | None = Field(
        description="When the repository was last embedded for semantic search."
    )
    created_at: datetime


class PullRequestRead(BaseModel):
    """A pull request DevPilot has seen."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    number: int
    title: str
    author_login: str
    state: PullRequestState
    head_sha: str
    base_sha: str
    head_ref: str
    base_ref: str
    created_at: datetime


class SyncRequest(BaseModel):
    """Which installation to reconcile against."""

    installation_id: int = Field(
        description="The GitHub App installation to sync repositories from."
    )


class SyncResponse(BaseModel):
    """What a synchronisation changed."""

    created: int
    updated: int
    deactivated: int


class InstallationRead(BaseModel):
    """A GitHub App installation visible to DevPilot."""

    id: int
    account_login: str | None
    repository_selection: str | None


class GitHubLinkStatus(BaseModel):
    """Whether the signed-in user has connected a GitHub account."""

    linked: bool
    github_login: str | None
    # Present only when the app slug is configured; the UI hides the button
    # rather than showing a link that leads nowhere.
    install_url: str | None = None
