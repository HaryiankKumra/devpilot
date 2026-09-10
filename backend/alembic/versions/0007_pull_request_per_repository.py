"""Scope pull request identity to the repository.

Follows from 0006. GitHub's pull request id is globally unique on GitHub, but
DevPilot now stores one repository row per owner, so two users tracking the same
repository legitimately hold the same pull request twice. The global constraint
made the second one fail with HTTP 500 when a webhook arrived.

Found by load testing, not by the unit suite: it needs two owners tracking one
repository *and* a delivery for it, which no single-user test creates.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_pull_requests_github_pr_id", "pull_requests", type_="unique")
    op.create_unique_constraint(
        "uq_pull_requests_repository_id_github_pr_id",
        "pull_requests",
        ["repository_id", "github_pr_id"],
    )


def downgrade() -> None:
    # Can fail if two repository rows now hold the same pull request, which is
    # exactly the state this migration exists to permit.
    op.drop_constraint(
        "uq_pull_requests_repository_id_github_pr_id", "pull_requests", type_="unique"
    )
    op.create_unique_constraint("uq_pull_requests_github_pr_id", "pull_requests", ["github_pr_id"])
