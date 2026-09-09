"""The `users` table: local DevPilot accounts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.repository import Repository

# The maximum length of an email address per RFC 5321.
EMAIL_MAX_LENGTH = 320


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A person with a DevPilot account.

    GitHub linkage (installation and OAuth identity) arrives in Milestone 3 and
    is deliberately absent here rather than being stubbed with unused columns.
    """

    __tablename__ = "users"

    # Stored lower-cased by the service layer so that `A@b.com` and `a@b.com`
    # cannot become two accounts. The unique index enforces the rest.
    email: Mapped[str] = mapped_column(
        String(EMAIL_MAX_LENGTH), unique=True, index=True, nullable=False
    )

    # An Argon2id digest, never the password. Sized generously: the encoded form
    # embeds the algorithm, version and cost parameters alongside salt and hash,
    # so it grows if those parameters are ever raised.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Lets an account be disabled without deleting it, which would cascade to
    # the review history that other users may still need to read.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    repositories: Mapped[list[Repository]] = relationship(
        back_populates="owner",
        # Deleting a user removes the repositories they connected; leaving them
        # orphaned would break the ownership checks that authorise access.
        cascade="all, delete-orphan",
        # `passive_deletes` lets the database's ON DELETE CASCADE do the work.
        # Without it SQLAlchemy SELECTs every child row and issues one DELETE
        # each, which is slower and redundant when the foreign key already
        # guarantees the cascade.
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"
