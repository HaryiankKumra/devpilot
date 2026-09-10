"""Retrieving relevant repository context with pgvector.

Given a pull request diff, find the stored chunks most likely to help review it:
the definition of a function being called, the module that owns a constant, the
existing convention the change should follow.

Three decisions shape the query.

**Scoped to one repository.** Similarity search across every repository would
happily return a chunk from someone else's codebase. The filter is a correctness
and privacy requirement, not an optimisation.

**A distance ceiling.** A nearest-neighbour search always returns its k nearest
rows, however far away they are. Without a ceiling, a query about coupon logic
in a repository containing nothing similar still returns six chunks, and the
model is handed irrelevant code presented as relevant context.

**Chunks from the changed files themselves are excluded.** They are already in
the prompt as the diff. Retrieving them again spends the context budget on
duplicates instead of the surrounding code the model cannot otherwise see.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models.code_chunk import CodeChunk
from app.integrations.embeddings.provider import EmbeddingProvider
from app.services.diff import ParsedDiff

logger = get_logger(__name__)

# Characters of diff used to build the search query. The whole diff would drown
# the signal: a query is a summary of what to look for, not the material itself.
MAX_QUERY_CHARS = 4_000


@dataclass(frozen=True)
class RetrievedChunk:
    """A stored chunk and how close it was to the query."""

    file_path: str
    start_line: int
    end_line: int
    content: str
    distance: float

    def describe_location(self) -> str:
        return f"{self.file_path}:{self.start_line}-{self.end_line}"


def build_query_text(diff: ParsedDiff) -> str:
    """Turn a diff into the text to search with.

    Only added lines are used, with the diff markers stripped. Removed lines
    describe code that no longer exists, and context lines are mostly unchanged
    boilerplate -- including either pulls the query towards the wrong thing.
    """
    parts: list[str] = []

    for file_diff in diff.reviewable_files:
        parts.append(file_diff.path)
        for line in file_diff.patch_lines:
            if line.startswith("+"):
                parts.append(line[1:].strip())

    query = "\n".join(part for part in parts if part)
    return query[:MAX_QUERY_CHARS]


def retrieve_context(
    session: Session,
    *,
    repository_id: uuid.UUID,
    diff: ParsedDiff,
    embedder: EmbeddingProvider,
    settings: Settings | None = None,
) -> list[RetrievedChunk]:
    """Return the stored chunks most relevant to this diff."""
    settings = settings or get_settings()

    query_text = build_query_text(diff)
    if not query_text.strip():
        return []

    query_vector = embedder.embed_query(query_text)
    changed_paths = {file_diff.path for file_diff in diff.files}

    # `cosine_distance` maps to pgvector's `<=>` operator, which is what the
    # HNSW index is built for. A different operator here would silently make
    # the index unusable and turn every search into a sequential scan.
    distance = CodeChunk.embedding.cosine_distance(query_vector).label("distance")

    statement = (
        select(CodeChunk, distance)
        .where(CodeChunk.repository_id == repository_id)
        .where(distance <= settings.retrieval_max_distance)
        .order_by(distance)
        .limit(settings.retrieval_top_k)
    )
    if changed_paths:
        statement = statement.where(CodeChunk.file_path.notin_(changed_paths))

    rows = session.execute(statement).all()

    results = [
        RetrievedChunk(
            file_path=chunk.file_path,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            content=chunk.content,
            distance=float(chunk_distance),
        )
        for chunk, chunk_distance in rows
    ]

    logger.info(
        "retrieval.completed",
        repository_id=str(repository_id),
        retrieved=len(results),
        closest=round(results[0].distance, 4) if results else None,
    )
    return results
