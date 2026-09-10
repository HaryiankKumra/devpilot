"""Typed views of the GitHub API responses DevPilot consumes.

GitHub returns large objects with dozens of fields; these models declare only
the parts DevPilot actually uses. `extra="ignore"` is what makes that safe --
GitHub adds fields regularly, and a strict model would start failing the day
they do.

Validating at the boundary means the rest of the codebase works with typed
attributes rather than `payload["repository"]["owner"]["login"]` chains that
raise `KeyError` deep inside a worker.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GitHubModel(BaseModel):
    """Base for every GitHub payload model."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class GitHubAccount(GitHubModel):
    """A user or organisation."""

    id: int
    login: str
    avatar_url: str | None = None
    type: str | None = None


class GitHubRepository(GitHubModel):
    """A repository as returned by the REST API."""

    id: int
    # `owner/name`. Mutable -- repositories are renamed and transferred -- so
    # `id` is what DevPilot keys on.
    full_name: str
    name: str
    private: bool = False
    default_branch: str = "main"
    owner: GitHubAccount | None = None
    html_url: str | None = None


class GitHubInstallation(GitHubModel):
    """An installation of the DevPilot App on a user or organisation."""

    id: int
    account: GitHubAccount | None = None
    repository_selection: str | None = Field(default=None, description="`all` or `selected`.")


class GitHubInstallationToken(GitHubModel):
    """A short-lived token scoped to one installation.

    GitHub issues these for one hour. DevPilot caches them and refreshes early
    rather than waiting for a 401.
    """

    token: str
    expires_at: datetime


class GitHubPullRequestRef(GitHubModel):
    """One side of a pull request: the branch and the commit it points at."""

    ref: str
    sha: str


class GitHubPullRequest(GitHubModel):
    """A pull request."""

    id: int
    number: int
    title: str
    state: str
    draft: bool = False
    merged: bool = False
    user: GitHubAccount | None = None
    head: GitHubPullRequestRef
    base: GitHubPullRequestRef
    html_url: str | None = None


class GitHubOAuthToken(GitHubModel):
    """The result of exchanging an OAuth code for a user access token."""

    access_token: str
    token_type: str = "bearer"
    scope: str = ""
