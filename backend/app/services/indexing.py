"""Indexing a repository into pgvector.

Walks the repository at a commit, chunks every indexable file, embeds the
chunks, and stores them with their file path and line range.

The design decision that matters is **content-hash deduplication**. Embedding is
the expensive part -- it is a paid API call per batch -- and most of a repository
is unchanged between indexing runs. Hashing each chunk's content and skipping
the ones already stored turns re-indexing from "embed everything again" into
"embed what changed", which is the difference between a re-index costing pennies
and costing what the first one did.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models.code_chunk import CodeChunk
from app.db.models.repository import Repository
from app.integrations.embeddings.provider import EmbeddingProvider
from app.integrations.github.client import GitHubClient
from app.services.chunking import CodeChunkContent, chunk_file, is_indexable

logger = get_logger(__name__)


@dataclass(frozen=True)
class IndexResult:
    """What one indexing run changed."""

    files_seen: int
    files_indexed: int
    chunks_created: int
    chunks_reused: int
    chunks_removed: int

    @property
    def total_chunks(self) -> int:
        return self.chunks_created + self.chunks_reused


def index_repository(
    session: Session,
    *,
    repository: Repository,
    commit_sha: str,
    client: GitHubClient,
    embedder: EmbeddingProvider,
    settings: Settings | None = None,
) -> IndexResult:
    """Index a repository at one commit.

    Returns without embedding anything when every chunk is already stored, which
    is the common case for a re-index after a small change.
    """
    settings = settings or get_settings()

    if repository.installation_id is None:
        raise ValueError(
            f"{repository.full_name} has no GitHub App installation, so it cannot be indexed."
        )

    paths = client.list_repository_files(
        repository.installation_id, repository.full_name, commit_sha
    )
    indexable = [path for path in paths if is_indexable(path)][: settings.max_indexed_files]

    chunks = _collect_chunks(
        repository=repository,
        commit_sha=commit_sha,
        paths=indexable,
        client=client,
    )

    existing = _existing_hashes(session, repository_id=repository.id)
    new_chunks = [chunk for chunk in chunks if chunk.content_hash not in existing]
    reused = len(chunks) - len(new_chunks)

    if new_chunks:
        _embed_and_store(
            session,
            repository=repository,
            commit_sha=commit_sha,
            chunks=new_chunks,
            embedder=embedder,
        )

    removed = _remove_stale(
        session,
        repository_id=repository.id,
        current_hashes={chunk.content_hash for chunk in chunks},
    )

    repository.indexed_commit_sha = commit_sha
    repository.indexed_at = datetime.now(UTC)

    result = IndexResult(
        files_seen=len(paths),
        files_indexed=len(indexable),
        chunks_created=len(new_chunks),
        chunks_reused=reused,
        chunks_removed=removed,
    )
    logger.info(
        "indexing.completed",
        repository=repository.full_name,
        commit_sha=commit_sha,
        **result.__dict__,
    )
    return result


def _collect_chunks(
    *,
    repository: Repository,
    commit_sha: str,
    paths: list[str],
    client: GitHubClient,
) -> list[CodeChunkContent]:
    """Fetch and chunk every indexable file."""
    assert repository.installation_id is not None  # checked by the caller
    chunks: list[CodeChunkContent] = []

    for path in paths:
        content = client.get_file_content(
            repository.installation_id, repository.full_name, path, commit_sha
        )
        if content is None:
            # Unreadable or binary despite the extension. Skip rather than fail
            # the whole index for one file.
            continue
        chunks.extend(chunk_file(path, content))

    return chunks


def _existing_hashes(session: Session, *, repository_id: uuid.UUID) -> set[str]:
    """Content hashes already stored for this repository."""
    rows = session.execute(
        select(CodeChunk.content_hash).where(CodeChunk.repository_id == repository_id)
    ).scalars()
    return set(rows)


def _embed_and_store(
    session: Session,
    *,
    repository: Repository,
    commit_sha: str,
    chunks: list[CodeChunkContent],
    embedder: EmbeddingProvider,
) -> None:
    """Embed new chunks and write them."""
    vectors = embedder.embed_documents([chunk.content for chunk in chunks])

    if len(vectors) != len(chunks):
        # Storing a chunk against the wrong vector would corrupt every later
        # retrieval, silently and permanently.
        raise ValueError(
            f"The embedding provider returned {len(vectors)} vectors for {len(chunks)} chunks."
        )

    for chunk, vector in zip(chunks, vectors, strict=True):
        session.add(
            CodeChunk(
                repository_id=repository.id,
                file_path=chunk.file_path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                content=chunk.content,
                content_hash=chunk.content_hash,
                commit_sha=commit_sha,
                embedding=vector,
            )
        )
    session.flush()


def _remove_stale(session: Session, *, repository_id: uuid.UUID, current_hashes: set[str]) -> int:
    """Delete chunks whose content no longer exists in the repository.

    Without this, deleted code stays retrievable forever and the model is shown
    functions that no longer exist -- confidently, and with a file path that
    makes them look current.
    """
    statement = delete(CodeChunk).where(CodeChunk.repository_id == repository_id)

    if current_hashes:
        # Keep what is still present. With no current hashes the repository has
        # nothing indexable left, so every stored chunk is stale and the filter
        # is omitted entirely rather than expressed as a always-true clause.
        statement = statement.where(CodeChunk.content_hash.notin_(current_hashes))

    result = cast(CursorResult[Any], session.execute(statement))
    return result.rowcount
