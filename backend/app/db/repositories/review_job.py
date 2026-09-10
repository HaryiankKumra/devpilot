"""Data access for `review_jobs` and `webhook_events`."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import ReviewJobStatus
from app.db.models.review_job import ReviewJob
from app.db.models.webhook_event import WebhookEvent


class ReviewJobStore:
    """Queries and writes for review attempts."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, job_id: uuid.UUID) -> ReviewJob | None:
        return self._session.get(ReviewJob, job_id)

    def add(self, job: ReviewJob) -> ReviewJob:
        self._session.add(job)
        self._session.flush()
        return job

    def list_for_pull_request(self, pull_request_id: uuid.UUID) -> Sequence[ReviewJob]:
        statement = (
            select(ReviewJob)
            .where(ReviewJob.pull_request_id == pull_request_id)
            .order_by(ReviewJob.created_at.desc())
        )
        return self._session.execute(statement).scalars().all()

    def find_active_for_commit(self, pull_request_id: uuid.UUID, head_sha: str) -> ReviewJob | None:
        """An unfinished job already covering this exact commit, if one exists.

        Guards against duplicate work when GitHub sends several deliveries that
        resolve to the same commit -- `synchronize` and `ready_for_review` can
        arrive together without the head moving.
        """
        statement = select(ReviewJob).where(
            ReviewJob.pull_request_id == pull_request_id,
            ReviewJob.head_sha == head_sha,
            ReviewJob.status.in_([ReviewJobStatus.QUEUED, ReviewJobStatus.RUNNING]),
        )
        return self._session.execute(statement).scalars().first()

    def cancel_superseded(self, pull_request_id: uuid.UUID, *, keep_head_sha: str) -> int:
        """Cancel unfinished jobs for commits this pull request has moved past.

        A push replaces the diff, so reviewing the old commit spends an LLM call
        on a version nobody will merge. The rows stay for the audit trail; only
        their status changes.
        """
        superseded = (
            self._session.execute(
                select(ReviewJob).where(
                    ReviewJob.pull_request_id == pull_request_id,
                    ReviewJob.head_sha != keep_head_sha,
                    ReviewJob.status.in_([ReviewJobStatus.QUEUED, ReviewJobStatus.RUNNING]),
                )
            )
            .scalars()
            .all()
        )
        for job in superseded:
            job.status = ReviewJobStatus.CANCELLED
        return len(superseded)


class WebhookEventStore:
    """Queries and writes for the GitHub delivery ledger."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_delivery_id(self, delivery_id: str) -> WebhookEvent | None:
        statement = select(WebhookEvent).where(WebhookEvent.delivery_id == delivery_id)
        return self._session.execute(statement).scalar_one_or_none()

    def add(self, event: WebhookEvent) -> WebhookEvent:
        self._session.add(event)
        self._session.flush()
        return event
