"""The `code_chunks` table: embedded repository content for retrieval."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.repository import Repository

FILE_PATH_MAX_LENGTH = 1024

# Dimensionality of `voyage-code-3` output. Fixed in the schema because
# pgvector needs it at column-definition time, and because similarity is only
# meaningful between vectors from the same model: changing models means a
# migration and a full re-index, not a mixed table.
EMBEDDING_DIMENSIONS = 1024


class CodeChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A slice of a repository file, stored with its embedding.

    The location metadata is not decoration. Without `file_path` and the line
    range, a retrieved chunk is anonymous text: the LLM cannot cite it, and a
    finding derived from it cannot be anchored to a line for posting back to
    GitHub.
    """

    __tablename__ = "code_chunks"
    __table_args__ = (
        # Re-indexing an unchanged file must not duplicate rows. Hashing the
        # content makes that check cheap, and independent of line drift
        # elsewhere in the file.
        UniqueConstraint(
            "repository_id", "file_path", "content_hash", name="uq_code_chunks_identity"
        ),
        CheckConstraint("end_line >= start_line", name="line_range_ordered"),
        CheckConstraint("start_line >= 1", name="start_line_positive"),
        # Retrieval is always scoped to one repository, so the lookup path is
        # repository first, then file.
        Index("ix_code_chunks_repository_id_file_path", "repository_id", "file_path"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )

    file_path: Mapped[str] = mapped_column(String(FILE_PATH_MAX_LENGTH), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 of `content`, backing the de-duplication constraint above.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # The commit this chunk was extracted from, so a stale index is detectable.
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)

    # PostgreSQL-only: there is no portable equivalent, and approximate nearest
    # neighbour search is the entire point of the column. Tests that touch this
    # table are marked `integration` and need real PostgreSQL with pgvector.
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)

    repository: Mapped[Repository] = relationship(back_populates="code_chunks")

    def __repr__(self) -> str:
        return (
            f"<CodeChunk id={self.id} path={self.file_path!r} "
            f"lines={self.start_line}-{self.end_line}>"
        )
