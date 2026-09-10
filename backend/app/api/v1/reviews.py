"""Review and pull request endpoints for the dashboard.

Every query is scoped to the signed-in user by joining through `repositories`.
That join is the authorisation check: a review belongs to a pull request, which
belongs to a repository, which has an owner. Filtering anywhere less thorough
would let a user read another user's findings by guessing a UUID.

Something not present is deliberate: none of these accept a `repository_id` from
the client and trust it. The ownership filter is always applied server-side.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession
from app.core.exceptions import NotFoundError
from app.db.models.finding import Finding
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.models.review import Review
from app.db.models.review_job import ReviewJob
from app.schemas.review import (
    FindingRead,
    PullRequestDetail,
    ReviewDetail,
    ReviewJobRead,
    ReviewListItem,
    ReviewSummary,
)

router = APIRouter(tags=["reviews"])

# Severity order for display: worst first, because the reason to open a review
# is the worst thing in it.
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@router.get(
    "/reviews",
    response_model=list[ReviewListItem],
    summary="Recent reviews across every repository",
)
def list_reviews(
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
    repository_id: uuid.UUID | None = None,
) -> list[ReviewListItem]:
    """The dashboard feed, newest first."""
    statement = (
        select(Review, PullRequest, Repository)
        .join(PullRequest, Review.pull_request_id == PullRequest.id)
        .join(Repository, PullRequest.repository_id == Repository.id)
        .where(Repository.owner_id == user.id)
        .order_by(Review.created_at.desc())
        .limit(limit)
    )
    if repository_id is not None:
        statement = statement.where(Repository.id == repository_id)

    return [
        ReviewListItem(
            **ReviewSummary.model_validate(review).model_dump(),
            repository_full_name=repository.full_name,
            pull_request_number=pull_request.number,
            pull_request_title=pull_request.title,
        )
        for review, pull_request, repository in session.execute(statement).all()
    ]


@router.get(
    "/reviews/{review_id}",
    response_model=ReviewDetail,
    summary="One review with its findings",
    responses={404: {"description": "No such review for this user."}},
)
def get_review(review_id: uuid.UUID, user: CurrentUser, session: DbSession) -> ReviewDetail:
    """A single review, findings worst-first."""
    statement = (
        select(Review)
        .join(PullRequest, Review.pull_request_id == PullRequest.id)
        .join(Repository, PullRequest.repository_id == Repository.id)
        .where(Review.id == review_id, Repository.owner_id == user.id)
        # Load findings in the same round trip rather than lazily per attribute
        # access, which would be an N+1 the moment the response is serialised.
        .options(selectinload(Review.findings))
    )
    review = session.execute(statement).scalar_one_or_none()

    if review is None:
        # 404 rather than 403 for someone else's review: confirming it exists is
        # itself information.
        raise NotFoundError("Review not found.")

    findings = sorted(
        review.findings,
        key=lambda f: (SEVERITY_RANK.get(f.severity.value, 99), f.file_path, f.line or 0),
    )

    return ReviewDetail(
        **ReviewSummary.model_validate(review).model_dump(),
        findings=[FindingRead.model_validate(finding) for finding in findings],
        prompt_tokens=review.prompt_tokens,
        completion_tokens=review.completion_tokens,
    )


@router.get(
    "/pull-requests/{pull_request_id}",
    response_model=PullRequestDetail,
    summary="One pull request with its attempts and results",
    responses={404: {"description": "No such pull request for this user."}},
)
def get_pull_request(
    pull_request_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> PullRequestDetail:
    """Everything known about one pull request.

    Includes failed jobs, with their error detail: "why has this not been
    reviewed?" is the question this page exists to answer.
    """
    statement = (
        select(PullRequest, Repository)
        .join(Repository, PullRequest.repository_id == Repository.id)
        .where(PullRequest.id == pull_request_id, Repository.owner_id == user.id)
    )
    row = session.execute(statement).first()

    if row is None:
        raise NotFoundError("Pull request not found.")

    pull_request, repository = row

    jobs = (
        session.execute(
            select(ReviewJob)
            .where(ReviewJob.pull_request_id == pull_request.id)
            .order_by(ReviewJob.created_at.desc())
        )
        .scalars()
        .all()
    )
    reviews = (
        session.execute(
            select(Review)
            .where(Review.pull_request_id == pull_request.id)
            .order_by(Review.created_at.desc())
        )
        .scalars()
        .all()
    )

    return PullRequestDetail(
        id=pull_request.id,
        repository_id=pull_request.repository_id,
        repository_full_name=repository.full_name,
        number=pull_request.number,
        title=pull_request.title,
        author_login=pull_request.author_login,
        state=pull_request.state,
        head_sha=pull_request.head_sha,
        head_ref=pull_request.head_ref,
        base_ref=pull_request.base_ref,
        created_at=pull_request.created_at,
        jobs=[ReviewJobRead.model_validate(job) for job in jobs],
        reviews=[ReviewSummary.model_validate(review) for review in reviews],
    )


@router.get(
    "/findings",
    response_model=list[FindingRead],
    summary="Findings across every review",
)
def list_findings(
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    severity: str | None = None,
) -> list[FindingRead]:
    """Every finding the signed-in user's repositories have produced."""
    statement = (
        select(Finding)
        .join(Review, Finding.review_id == Review.id)
        .join(PullRequest, Review.pull_request_id == PullRequest.id)
        .join(Repository, PullRequest.repository_id == Repository.id)
        .where(Repository.owner_id == user.id)
        .order_by(Finding.created_at.desc())
        .limit(limit)
    )
    if severity is not None:
        statement = statement.where(Finding.severity == severity)

    return [
        FindingRead.model_validate(finding)
        for finding in session.execute(statement).scalars().all()
    ]
