"""Make repository identity per owner rather than global.

DevPilot is multi-tenant: two users may each connect the same GitHub
repository, each with their own review history. A single global unique
constraint on `github_repo_id` made the second user's sync silently
match the first user's row, leaving them with an empty repository list
and nothing explaining why.

Found by an end-to-end browser test: a freshly registered user synced
successfully and saw nothing.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_repositories_github_repo_id", "repositories", type_="unique")
    op.create_unique_constraint(
        "uq_repositories_owner_id_github_repo_id",
        "repositories",
        ["owner_id", "github_repo_id"],
    )


def downgrade() -> None:
    # Reversing this can fail if two owners have since tracked the same
    # repository, which is exactly the state the upgrade exists to allow.
    op.drop_constraint("uq_repositories_owner_id_github_repo_id", "repositories", type_="unique")
    op.create_unique_constraint(
        "uq_repositories_github_repo_id", "repositories", ["github_repo_id"]
    )
