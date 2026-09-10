"""Link a DevPilot account to a GitHub identity.

Adds the columns populated by the OAuth flow. Both are nullable because an
account exists before it is linked, and unlinking must not require deleting it.

`github_id` gets a unique index so one GitHub identity cannot be attached to two
DevPilot accounts -- which would make it ambiguous who owns a connected
repository. `github_login` is deliberately *not* unique: GitHub logins are
renameable and reusable, so uniqueness there would be enforcing a guarantee
GitHub does not make.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("github_id", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("github_login", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_users_github_id"), "users", ["github_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_github_id"), table_name="users")
    op.drop_column("users", "github_login")
    op.drop_column("users", "github_id")
