"""The `findings` table: individual issues raised by a review."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import FindingCategory, FindingSeverity
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column_type

if TYPE_CHECKING:
    from app.db.models.review import Review

# Comfortably above any path Git will produce.
FILE_PATH_MAX_LENGTH = 1024


class Finding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single issue: what, where, how bad, and how sure the model is.

    Every field here originates from LLM output and has already passed Pydantic
    validation before reaching the database. The constraints below are the
    second line of defence: a schema change that loosened validation would still
    be caught here rather than corrupting the risk score.
    """

    __tablename__ = "findings"
    __table_args__ = (
        # A probability outside 0..1 would silently distort confidence filtering
        # when deciding what is worth posting back to GitHub.
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="confidence_range"),
        # Line numbers are 1-based; 0 or negative means the model invented one.
        CheckConstraint("line IS NULL OR line >= 1", name="line_positive"),
    )

    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), index=True, nullable=False
    )

    category: Mapped[FindingCategory] = mapped_column(
        enum_column_type(FindingCategory), index=True, nullable=False
    )
    severity: Mapped[FindingSeverity] = mapped_column(
        enum_column_type(FindingSeverity), index=True, nullable=False
    )

    file_path: Mapped[str] = mapped_column(String(FILE_PATH_MAX_LENGTH), nullable=False)
    # Nullable because a finding can legitimately concern a whole file rather
    # than one line; such findings cannot be posted as inline PR comments.
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The model's self-reported confidence. Used only to decide what is worth
    # posting to GitHub; it deliberately does not affect the risk score, which
    # stays a pure function of severity.
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # Whether this finding was posted back to the pull request, and as which
    # comment. Recorded so that a retry does not post the same comment twice.
    is_posted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    review: Mapped[Review] = relationship(back_populates="findings")

    def __repr__(self) -> str:
        return f"<Finding id={self.id} severity={self.severity} file={self.file_path!r}>"
