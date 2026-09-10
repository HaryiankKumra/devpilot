"""Resize the embedding column and add an HNSW index.

Two changes.

**1024 dimensions instead of 1536.** The column was sized before an embedding
model was chosen; `voyage-code-3` emits 1024. Because a vector column's width is
fixed at definition time and vectors from different models are not comparable
anyway, the column is dropped and recreated rather than altered -- any rows that
existed would have to be re-embedded regardless, so preserving them would
preserve nothing usable.

**An HNSW index.** Without one, every similarity search is a sequential scan
that reads and compares every chunk in the table. That is fine for a demo and
untenable for a repository of any size. HNSW rather than IVFFlat because it does
not need to be built against existing data to be useful -- IVFFlat's list
centroids are computed at build time, so an index created on an empty table (as
here) is worthless until rebuilt.

`vector_cosine_ops` matches how the query measures distance. An index built for
a different operator is silently ignored by the planner, which is a
uniquely annoying way to lose performance.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSIONS = 1024
PREVIOUS_DIMENSIONS = 1536
INDEX_NAME = "ix_code_chunks_embedding_hnsw"


def upgrade() -> None:
    # Any existing rows hold 1536-dimension vectors from a model no longer in
    # use. They cannot be compared with new ones, so they are removed rather
    # than left to poison every retrieval.
    op.execute("DELETE FROM code_chunks")

    op.drop_column("code_chunks", "embedding")
    op.add_column(
        "code_chunks",
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(dim=EMBEDDING_DIMENSIONS),
            nullable=False,
        ),
    )

    op.execute(f"CREATE INDEX {INDEX_NAME} ON code_chunks USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
    op.execute("DELETE FROM code_chunks")
    op.drop_column("code_chunks", "embedding")
    op.add_column(
        "code_chunks",
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(dim=PREVIOUS_DIMENSIONS),
            nullable=False,
        ),
    )
