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


# --- Webhook event payloads --------------------------------------------------
# GitHub sends a different envelope per event type. Only the events DevPilot
# acts on are modelled; everything else is recorded and ignored without being
# parsed, so an unmodelled event can never crash the endpoint.


class GitHubInstallationRef(GitHubModel):
    """The `installation` stub embedded in most webhook payloads."""

    id: int


class InstallationEvent(GitHubModel):
    """`installation` and `installation_repositories` deliveries.

    `sender` is the person who clicked install. It is how DevPilot decides which
    local account owns the repositories: their GitHub id was recorded when they
    linked their account.
    """

    action: str
    installation: GitHubInstallation
    sender: GitHubAccount | None = None

    # Present on `installation` created/deleted.
    repositories: list[GitHubRepository] = Field(default_factory=list)
    # Present on `installation_repositories` added/removed.
    repositories_added: list[GitHubRepository] = Field(default_factory=list)
    repositories_removed: list[GitHubRepository] = Field(default_factory=list)


class PullRequestEvent(GitHubModel):
    """A `pull_request` delivery."""

    action: str
    number: int
    pull_request: GitHubPullRequest
    repository: GitHubRepository
    installation: GitHubInstallationRef | None = None
    sender: GitHubAccount | None = None


# --- Outgoing: what DevPilot posts back --------------------------------------


class ReviewComment(GitHubModel):
    """One inline comment on a pull request."""

    path: str
    line: int = Field(ge=1, description="Line in the new file, 1-based.")
    body: str

    def to_payload(self) -> dict[str, object]:
        """Render for the reviews API.

        `side: RIGHT` anchors the comment to the *new* version of the file. The
        default is also RIGHT, but stating it prevents a comment silently
        landing on the pre-change side after an API default ever shifts.
        """
        return {"path": self.path, "line": self.line, "side": "RIGHT", "body": self.body}


class PostedReview(GitHubModel):
    """The result of posting a review."""

    review_id: int
    html_url: str | None = None
    comment_count: int = 0
