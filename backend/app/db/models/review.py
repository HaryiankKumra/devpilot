"""The `reviews` table: the result of a successful review job."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.finding import Finding
    from app.db.models.pull_request import PullRequest
    from app.db.models.review_job import ReviewJob

RISK_SCORE_MIN = 0
RISK_SCORE_MAX = 100


class Review(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One completed review of one pull request at one commit."""

    __tablename__ = "reviews"
    __table_args__ = (
        # The score is computed, so an out-of-range value means a bug in the
        # scoring code. Enforce the invariant where it cannot be bypassed.
        CheckConstraint(
            f"risk_score >= {RISK_SCORE_MIN} AND risk_score <= {RISK_SCORE_MAX}",
            name="risk_score_range",
        ),
    )

    # One review per job: the job is the attempt, this is its single result.
    review_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_jobs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="CASCADE"), index=True, nullable=False
    )

    summary: Mapped[str] = mapped_column(Text, nullable=False)

    # Computed in Python from the findings' severities -- never taken from the
    # LLM, so the same findings always produce the same score.
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)

    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)

    # Which model produced this, so results stay interpretable after a model
    # upgrade changes the character of the findings.
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The GitHub review this was posted as, or NULL if it was never posted
    # (posting disabled, nothing confident enough, or GitHub refused it).
    github_review_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    review_job: Mapped[ReviewJob] = relationship(back_populates="review")
    pull_request: Mapped[PullRequest] = relationship(back_populates="reviews")
    findings: Mapped[list[Finding]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
        # `passive_deletes` lets the database's ON DELETE CASCADE do the work.
        # Without it SQLAlchemy SELECTs every child row and issues one DELETE
        # each, which is slower and redundant when the foreign key already
        # guarantees the cascade.
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Review id={self.id} risk_score={self.risk_score}>"
