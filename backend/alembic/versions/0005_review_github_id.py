"""Record which GitHub review a stored review was posted as.

NULL means it was never posted -- posting disabled, nothing confident enough to
say, or GitHub refused it. Keeping the distinction lets the dashboard show "not
posted" rather than implying every review reached the pull request.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reviews", sa.Column("github_review_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("reviews", "github_review_id")
