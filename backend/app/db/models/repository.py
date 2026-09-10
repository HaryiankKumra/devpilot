"""The `repositories` table: GitHub repositories DevPilot watches."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.code_chunk import CodeChunk
    from app.db.models.pull_request import PullRequest
    from app.db.models.user import User


class Repository(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A repository with the DevPilot GitHub App installed."""

    __tablename__ = "repositories"
    __table_args__ = (
        # Unique *per owner*, not globally. GitHub's numeric id is the identity
        # that survives renames, but DevPilot is multi-tenant: two users may
        # each connect the same repository, and a global constraint silently
        # gave the second one an empty list with no explanation.
        UniqueConstraint(
            "owner_id", "github_repo_id", name="uq_repositories_owner_id_github_repo_id"
        ),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    # GitHub ids exceed 32 bits, so BigInteger rather than Integer.
    github_repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # `owner/name`. Mutable: repositories get renamed and transferred, so this
    # is refreshed from webhooks and never used as a key.
    full_name: Mapped[str] = mapped_column(String(512), index=True, nullable=False)

    default_branch: Mapped[str] = mapped_column(String(255), default="main", nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Identifies which GitHub App installation to mint a token for when calling
    # the API on this repository's behalf.
    installation_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)

    # Cleared when the app is uninstalled, so reviews stop without losing history.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # When the repository was last embedded into `code_chunks`. NULL means it
    # has never been indexed, so retrieval must fall back to the diff alone.
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The commit the current index reflects, so re-indexing can be incremental.
    indexed_commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    owner: Mapped[User] = relationship(back_populates="repositories")
    pull_requests: Mapped[list[PullRequest]] = relationship(
        back_populates="repository",
        cascade="all, delete-orphan",
        # `passive_deletes` lets the database's ON DELETE CASCADE do the work.
        # Without it SQLAlchemy SELECTs every child row and issues one DELETE
        # each, which is slower and redundant when the foreign key already
        # guarantees the cascade.
        passive_deletes=True,
    )
    code_chunks: Mapped[list[CodeChunk]] = relationship(
        back_populates="repository",
        cascade="all, delete-orphan",
        # `passive_deletes` lets the database's ON DELETE CASCADE do the work.
        # Without it SQLAlchemy SELECTs every child row and issues one DELETE
        # each, which is slower and redundant when the foreign key already
        # guarantees the cascade.
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Repository id={self.id} full_name={self.full_name!r}>"
