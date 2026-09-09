"""The `review_jobs` table: one row per review *attempt*."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ReviewJobStatus
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column_type

if TYPE_CHECKING:
    from app.db.models.pull_request import PullRequest
    from app.db.models.review import Review

DEFAULT_MAX_ATTEMPTS = 3


class ReviewJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A unit of asynchronous work: review one pull request at one commit.

    Kept separate from `reviews` because an attempt and a result are different
    things. Attempts fail, retry and carry error detail; results do not. Merging
    them would make "the review failed" indistinguishable from "the review found
    nothing", and would leave nowhere to record why a failure happened.
    """

    __tablename__ = "review_jobs"

    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # The delivery that caused this job, for tracing a review back to its cause.
    webhook_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("webhook_events.id", ondelete="SET NULL"), nullable=True
    )

    # The commit this job reviews. Recorded on the job, not just the pull
    # request, because the PR's head moves on while the job is still running.
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)

    status: Mapped[ReviewJobStatus] = mapped_column(
        enum_column_type(ReviewJobStatus),
        default=ReviewJobStatus.QUEUED,
        index=True,
        nullable=False,
    )

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=DEFAULT_MAX_ATTEMPTS, nullable=False)

    # Celery's task id, so a row can be traced to worker logs and a running
    # task can be revoked when a newer push supersedes it.
    celery_task_id: Mapped[str | None] = mapped_column(String(155), index=True, nullable=True)

    # Failure detail is split: the type is low-cardinality and groupable
    # ("how often does the LLM time out?"), the message is human-readable, and
    # the traceback is for debugging one specific failure.
    error_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_traceback: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pull_request: Mapped[PullRequest] = relationship(back_populates="review_jobs")
    review: Mapped[Review | None] = relationship(
        back_populates="review_job",
        cascade="all, delete-orphan",
        uselist=False,
        # `passive_deletes` lets the database's ON DELETE CASCADE do the work.
        # Without it SQLAlchemy SELECTs every child row and issues one DELETE
        # each, which is slower and redundant when the foreign key already
        # guarantees the cascade.
        passive_deletes=True,
    )

    @property
    def can_retry(self) -> bool:
        """True when another attempt is still permitted."""
        return self.attempts < self.max_attempts

    def __repr__(self) -> str:
        return f"<ReviewJob id={self.id} status={self.status} attempts={self.attempts}>"
