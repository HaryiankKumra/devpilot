"""Enable pgvector and create the code_chunks table.

Split from the initial migration because this is the only part of the schema
that needs a PostgreSQL extension. Keeping it separate means a database without
pgvector available fails here, with an obvious name, instead of making the
initial migration look broken.

`CREATE EXTENSION` requires privileges a plain application role usually lacks,
so on a managed database it may need to be run once by an administrator; the
`IF NOT EXISTS` makes that pre-provisioned case a no-op rather than an error.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "code_chunks",
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "end_line >= start_line", name=op.f("ck_code_chunks_line_range_ordered")
        ),
        sa.CheckConstraint("start_line >= 1", name=op.f("ck_code_chunks_start_line_positive")),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_code_chunks_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_code_chunks")),
        sa.UniqueConstraint(
            "repository_id", "file_path", "content_hash", name="uq_code_chunks_identity"
        ),
    )
    op.create_index(
        op.f("ix_code_chunks_repository_id"), "code_chunks", ["repository_id"], unique=False
    )
    op.create_index(
        "ix_code_chunks_repository_id_file_path",
        "code_chunks",
        ["repository_id", "file_path"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_code_chunks_repository_id_file_path", table_name="code_chunks")
    op.drop_index(op.f("ix_code_chunks_repository_id"), table_name="code_chunks")
    op.drop_table("code_chunks")
    # The extension is deliberately not dropped: other schemas in the same
    # database may be using it, and dropping it would take their columns with it.
