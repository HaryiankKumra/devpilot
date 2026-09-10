"""Integration tests for indexing and pgvector retrieval.

Marked `integration` because they need a real PostgreSQL with the `vector`
extension -- there is no SQLite equivalent, and a mock would prove nothing about
the thing under test, which is the database's own similarity search.

Every test runs inside a transaction that is rolled back, so nothing written
here survives.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.code_chunk import CodeChunk
from app.db.models.repository import Repository
from app.db.models.user import User
from app.integrations.embeddings.mock import MockEmbeddingProvider
from app.integrations.github.mock import (
    MOCK_INSTALLATION_ID,
    MOCK_PULL_REQUEST_REPOSITORY,
    MockGitHubClient,
)
from app.services.chunking import chunk_file
from app.services.diff import parse_unified_diff
from app.services.indexing import index_repository
from app.services.retrieval import build_query_text, retrieve_context

pytestmark = pytest.mark.integration

COMMIT = "a" * 40
DIMENSIONS = 1024


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, embedding_dimensions=DIMENSIONS)


@pytest.fixture
def embedder() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(DIMENSIONS)


@pytest.fixture
def repository(pg_session: Session) -> Repository:
    """An owner and repository that exist only for this transaction."""
    user = User(email=f"integration-{uuid.uuid4()}@example.com", hashed_password="$argon2id$fake")
    pg_session.add(user)
    pg_session.flush()

    row = Repository(
        owner_id=user.id,
        # Random so concurrent runs cannot collide on the unique constraint.
        github_repo_id=int(uuid.uuid4().int % 1_000_000_000),
        full_name=MOCK_PULL_REQUEST_REPOSITORY,
        installation_id=MOCK_INSTALLATION_ID,
    )
    pg_session.add(row)
    pg_session.flush()
    return row


class TestIndexing:
    def test_stores_chunks_with_their_location(
        self,
        pg_session: Session,
        repository: Repository,
        embedder: MockEmbeddingProvider,
        settings: Settings,
    ) -> None:
        result = index_repository(
            pg_session,
            repository=repository,
            commit_sha=COMMIT,
            client=MockGitHubClient(),
            embedder=embedder,
            settings=settings,
        )

        assert result.chunks_created > 0
        stored = pg_session.execute(select(CodeChunk)).scalars().all()
        assert stored
        for chunk in stored:
            assert chunk.file_path
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line
            assert len(chunk.embedding) == DIMENSIONS

    def test_marks_the_repository_indexed(
        self,
        pg_session: Session,
        repository: Repository,
        embedder: MockEmbeddingProvider,
        settings: Settings,
    ) -> None:
        index_repository(
            pg_session,
            repository=repository,
            commit_sha=COMMIT,
            client=MockGitHubClient(),
            embedder=embedder,
            settings=settings,
        )

        assert repository.indexed_at is not None
        assert repository.indexed_commit_sha == COMMIT

    def test_re_indexing_reuses_unchanged_chunks(
        self,
        pg_session: Session,
        repository: Repository,
        embedder: MockEmbeddingProvider,
        settings: Settings,
    ) -> None:
        """Embedding is the expensive part; unchanged content must not be
        embedded twice."""
        first = index_repository(
            pg_session,
            repository=repository,
            commit_sha=COMMIT,
            client=MockGitHubClient(),
            embedder=embedder,
            settings=settings,
        )

        second = index_repository(
            pg_session,
            repository=repository,
            commit_sha="b" * 40,
            client=MockGitHubClient(),
            embedder=embedder,
            settings=settings,
        )

        assert first.chunks_created > 0
        assert second.chunks_created == 0
        assert second.chunks_reused == first.chunks_created

    def test_removes_chunks_whose_content_is_gone(
        self,
        pg_session: Session,
        repository: Repository,
        embedder: MockEmbeddingProvider,
        settings: Settings,
    ) -> None:
        """Otherwise deleted code stays retrievable forever, and the model is
        shown functions that no longer exist."""
        stale = chunk_file("checkout/removed.py", "def gone():\n    return 1\n")[0]
        pg_session.add(
            CodeChunk(
                repository_id=repository.id,
                file_path=stale.file_path,
                start_line=stale.start_line,
                end_line=stale.end_line,
                content=stale.content,
                content_hash=stale.content_hash,
                commit_sha="old",
                embedding=embedder.embed_documents([stale.content])[0],
            )
        )
        pg_session.flush()

        result = index_repository(
            pg_session,
            repository=repository,
            commit_sha=COMMIT,
            client=MockGitHubClient(),
            embedder=embedder,
            settings=settings,
        )

        assert result.chunks_removed == 1
        paths = {chunk.file_path for chunk in pg_session.execute(select(CodeChunk)).scalars()}
        assert "checkout/removed.py" not in paths


class TestRetrieval:
    @pytest.fixture(autouse=True)
    def _indexed(
        self,
        pg_session: Session,
        repository: Repository,
        embedder: MockEmbeddingProvider,
        settings: Settings,
    ) -> None:
        index_repository(
            pg_session,
            repository=repository,
            commit_sha=COMMIT,
            client=MockGitHubClient(),
            embedder=embedder,
            settings=settings,
        )

    def test_finds_the_module_the_change_refers_to(
        self,
        pg_session: Session,
        repository: Repository,
        embedder: MockEmbeddingProvider,
        settings: Settings,
    ) -> None:
        """The changed code reads `LOOKUP`, which is defined in another file.
        Surfacing that definition is the entire point of retrieval."""
        diff = parse_unified_diff(
            MockGitHubClient().get_pull_request_diff(
                MOCK_INSTALLATION_ID, MOCK_PULL_REQUEST_REPOSITORY, 42
            )
        )

        results = retrieve_context(
            pg_session,
            repository_id=repository.id,
            diff=diff,
            embedder=embedder,
            settings=settings,
        )

        assert results
        assert any("discounts" in chunk.file_path for chunk in results)

    def test_excludes_the_changed_files_themselves(
        self,
        pg_session: Session,
        repository: Repository,
        embedder: MockEmbeddingProvider,
        settings: Settings,
    ) -> None:
        """They are already in the prompt as the diff; retrieving them again
        spends the context budget on duplicates."""
        diff = parse_unified_diff(
            MockGitHubClient().get_pull_request_diff(
                MOCK_INSTALLATION_ID, MOCK_PULL_REQUEST_REPOSITORY, 42
            )
        )

        results = retrieve_context(
            pg_session,
            repository_id=repository.id,
            diff=diff,
            embedder=embedder,
            settings=settings,
        )

        assert all(chunk.file_path != "checkout/coupons.py" for chunk in results)

    def test_results_are_ordered_by_closeness(
        self,
        pg_session: Session,
        repository: Repository,
        embedder: MockEmbeddingProvider,
        settings: Settings,
    ) -> None:
        diff = parse_unified_diff(
            MockGitHubClient().get_pull_request_diff(
                MOCK_INSTALLATION_ID, MOCK_PULL_REQUEST_REPOSITORY, 42
            )
        )

        results = retrieve_context(
            pg_session,
            repository_id=repository.id,
            diff=diff,
            embedder=embedder,
            settings=settings,
        )

        assert [c.distance for c in results] == sorted(c.distance for c in results)

    def test_a_distance_ceiling_excludes_unrelated_chunks(
        self,
        pg_session: Session,
        repository: Repository,
        embedder: MockEmbeddingProvider,
    ) -> None:
        """Without a ceiling, a nearest-neighbour search always returns k rows
        however far away they are."""
        diff = parse_unified_diff(
            MockGitHubClient().get_pull_request_diff(
                MOCK_INSTALLATION_ID, MOCK_PULL_REQUEST_REPOSITORY, 42
            )
        )

        strict = retrieve_context(
            pg_session,
            repository_id=repository.id,
            diff=diff,
            embedder=embedder,
            settings=Settings(
                _env_file=None, embedding_dimensions=DIMENSIONS, retrieval_max_distance=0.01
            ),
        )

        assert strict == []

    def test_never_returns_another_repositorys_chunks(
        self,
        pg_session: Session,
        repository: Repository,
        embedder: MockEmbeddingProvider,
        settings: Settings,
    ) -> None:
        """A privacy requirement, not an optimisation."""
        diff = parse_unified_diff(
            MockGitHubClient().get_pull_request_diff(
                MOCK_INSTALLATION_ID, MOCK_PULL_REQUEST_REPOSITORY, 42
            )
        )

        results = retrieve_context(
            pg_session,
            repository_id=uuid.uuid4(),  # a repository with nothing indexed
            diff=diff,
            embedder=embedder,
            settings=settings,
        )

        assert results == []


class TestQueryText:
    def test_uses_added_lines_only(self) -> None:
        """Removed lines describe code that no longer exists, and context lines
        are mostly boilerplate -- either pulls the query the wrong way."""
        diff = parse_unified_diff(
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n+++ b/a.py\n"
            "@@ -1,3 +1,3 @@\n"
            " untouched_context_line\n"
            "-removed_marker_text\n"
            "+added_marker_text\n"
        )

        query = build_query_text(diff)

        assert "added_marker_text" in query
        assert "removed_marker_text" not in query

    def test_includes_the_file_path(self) -> None:
        diff = parse_unified_diff(
            "diff --git a/checkout/coupons.py b/checkout/coupons.py\n"
            "--- a/checkout/coupons.py\n+++ b/checkout/coupons.py\n"
            "@@ -1,1 +1,2 @@\n keep\n+added\n"
        )

        assert "checkout/coupons.py" in build_query_text(diff)
