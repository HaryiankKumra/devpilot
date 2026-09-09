"""The `pull_requests` table: pull requests DevPilot has seen."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import PullRequestState
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column_type

if TYPE_CHECKING:
    from app.db.models.repository import Repository
    from app.db.models.review import Review
    from app.db.models.review_job import ReviewJob


class PullRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A pull request, kept in sync from webhook deliveries."""

    __tablename__ = "pull_requests"
    __table_args__ = (
        # A PR number is unique only within its repository, so the natural key
        # is the pair. This is also what makes webhook handling idempotent at
        # the row level: a re-delivery updates rather than duplicates.
        UniqueConstraint("repository_id", "number", name="uq_pull_requests_repository_id_number"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )

    github_pr_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    # The number shown in the UI and used in API paths (`/pulls/42`).
    number: Mapped[int] = mapped_column(Integer, nullable=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    author_login: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[PullRequestState] = mapped_column(
        enum_column_type(PullRequestState), default=PullRequestState.OPEN, nullable=False
    )

    # The commit actually reviewed. A review is only valid for one head SHA;
    # a new push produces a new SHA and therefore needs a new review.
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    base_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    head_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    base_ref: Mapped[str] = mapped_column(String(255), nullable=False)

    repository: Mapped[Repository] = relationship(back_populates="pull_requests")
    review_jobs: Mapped[list[ReviewJob]] = relationship(
        back_populates="pull_request",
        cascade="all, delete-orphan",
        # `passive_deletes` lets the database's ON DELETE CASCADE do the work.
        # Without it SQLAlchemy SELECTs every child row and issues one DELETE
        # each, which is slower and redundant when the foreign key already
        # guarantees the cascade.
        passive_deletes=True,
    )
    reviews: Mapped[list[Review]] = relationship(
        back_populates="pull_request",
        cascade="all, delete-orphan",
        # `passive_deletes` lets the database's ON DELETE CASCADE do the work.
        # Without it SQLAlchemy SELECTs every child row and issues one DELETE
        # each, which is slower and redundant when the foreign key already
        # guarantees the cascade.
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<PullRequest id={self.id} number={self.number} state={self.state}>"
