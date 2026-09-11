"""Schemas for reviews, findings and jobs as the dashboard sees them."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    FindingCategory,
    FindingSeverity,
    PullRequestState,
    ReviewJobStatus,
)


class FindingRead(BaseModel):
    """One issue, as displayed."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: FindingCategory
    severity: FindingSeverity
    file_path: str
    line: int | None
    title: str
    description: str
    suggestion: str | None
    confidence: float
    is_posted: bool = Field(
        description="Whether this was commented on the pull request. Low-confidence "
        "and file-level findings are stored but not posted."
    )


class ReviewSummary(BaseModel):
    """A review without its findings, for lists."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    pull_request_id: uuid.UUID
    summary: str
    risk_score: int
    head_sha: str
    model_name: str
    github_review_id: int | None
    created_at: datetime


class ReviewDetail(ReviewSummary):
    """A review with everything it found."""

    findings: list[FindingRead] = Field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ReviewListItem(ReviewSummary):
    """A review plus the context needed to make sense of it in a list.

    The repository and pull request are denormalised into the response because
    a dashboard row showing only a UUID is useless, and making the browser fetch
    each one separately is the classic N+1 that makes a list page slow.
    """

    repository_full_name: str
    pull_request_number: int
    pull_request_title: str


class ReviewJobRead(BaseModel):
    """One review attempt, including why it failed."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: ReviewJobStatus
    head_sha: str
    attempts: int
    max_attempts: int
    error_type: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class PullRequestDetail(BaseModel):
    """A pull request with its attempts and results."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    repository_full_name: str
    number: int
    title: str
    author_login: str
    state: PullRequestState
    head_sha: str
    head_ref: str
    base_ref: str
    created_at: datetime

    jobs: list[ReviewJobRead] = Field(default_factory=list)
    reviews: list[ReviewSummary] = Field(default_factory=list)


class PublishResponse(BaseModel):
    """What happened when a stored review was (re)posted to GitHub."""

    posted: bool
    comment_count: int
    github_review_id: int | None
    html_url: str | None
    # Why nothing was posted, when nothing was. Never a secret; it names a
    # condition ("already posted", "no findings above the threshold"), not a
    # credential.
    skipped_reason: str | None
