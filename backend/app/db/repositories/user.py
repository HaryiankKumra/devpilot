"""Data access for the `users` table.

The service layer talks to this class instead of writing queries inline. That
keeps query construction in one place, and lets service tests substitute a fake
implementation instead of standing up a database.

Repositories here never commit. Transaction boundaries belong to the caller, so
that several repository calls can succeed or fail as one unit.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user import User


class UserRepository:
    """Queries and writes for `User` rows."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def normalise_email(email: str) -> str:
        """Return the canonical form used for storage and lookup.

        Addresses are compared case-insensitively, so `A@B.com` and `a@b.com`
        must not be able to become two accounts. Normalising on the way in means
        the unique index enforces that rather than merely hoping for it.
        """
        return email.strip().lower()

    def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self._session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        statement = select(User).where(User.email == self.normalise_email(email))
        return self._session.execute(statement).scalar_one_or_none()

    def email_exists(self, email: str) -> bool:
        statement = select(User.id).where(User.email == self.normalise_email(email))
        return self._session.execute(statement).first() is not None

    def add(self, user: User) -> User:
        """Stage a new user and flush so its generated columns are populated.

        Flushing (not committing) sends the INSERT and surfaces constraint
        violations here, while leaving the enclosing transaction open for the
        caller to commit or roll back.
        """
        self._session.add(user)
        self._session.flush()
        return user
