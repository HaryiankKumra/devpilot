"""Tests for chunking and the mock embedding provider.

The line metadata these produce is what lets a retrieved chunk be cited. An
off-by-one here means the model is shown code labelled with the wrong location.
"""

from __future__ import annotations

import pytest

from app.integrations.embeddings.mock import MockEmbeddingProvider
from app.services.chunking import (
    DEFAULT_CHUNK_LINES,
    DEFAULT_OVERLAP_LINES,
    MAX_LINE_CHARS,
    chunk_file,
    chunk_repository_files,
    is_indexable,
)


def numbered_source(lines: int) -> str:
    return "\n".join(f"line_{n}" for n in range(1, lines + 1))


class TestChunkBoundaries:
    def test_a_short_file_is_one_chunk(self) -> None:
        chunks = chunk_file("a.py", numbered_source(10))

        assert len(chunks) == 1
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 10

    def test_line_numbers_are_one_based_and_inclusive(self) -> None:
        """Matching how editors, diffs and review comments all count."""
        chunk = chunk_file("a.py", numbered_source(3))[0]

        assert chunk.start_line == 1
        assert chunk.end_line == 3
        assert chunk.line_count == 3
        assert chunk.content.splitlines()[0] == "line_1"
        assert chunk.content.splitlines()[-1] == "line_3"

    def test_a_long_file_splits(self) -> None:
        chunks = chunk_file("a.py", numbered_source(200))

        assert len(chunks) > 1

    def test_chunks_overlap(self) -> None:
        """A function straddling a boundary must be whole in one of them."""
        chunks = chunk_file("a.py", numbered_source(200))

        first, second = chunks[0], chunks[1]
        assert second.start_line <= first.end_line

    def test_the_overlap_is_the_configured_size(self) -> None:
        chunks = chunk_file("a.py", numbered_source(200), chunk_lines=50, overlap_lines=10)

        assert chunks[1].start_line == chunks[0].end_line - 10 + 1

    def test_every_line_appears_somewhere(self) -> None:
        """No line may be lost between chunks."""
        chunks = chunk_file("a.py", numbered_source(137), chunk_lines=40, overlap_lines=5)

        covered: set[int] = set()
        for chunk in chunks:
            covered.update(range(chunk.start_line, chunk.end_line + 1))

        assert covered == set(range(1, 138))

    def test_the_last_chunk_ends_at_the_last_line(self) -> None:
        chunks = chunk_file("a.py", numbered_source(137), chunk_lines=40, overlap_lines=5)

        assert chunks[-1].end_line == 137

    def test_content_matches_the_declared_line_range(self) -> None:
        """The metadata must describe the content, or a citation points at the
        wrong code."""
        source = numbered_source(150)
        for chunk in chunk_file("a.py", source, chunk_lines=40, overlap_lines=5):
            expected = source.splitlines()[chunk.start_line - 1 : chunk.end_line]
            assert chunk.content.splitlines() == expected


class TestChunkEdgeCases:
    def test_an_empty_file_yields_nothing(self) -> None:
        assert chunk_file("a.py", "") == []

    def test_a_whitespace_only_file_yields_nothing(self) -> None:
        """Blank chunks carry no meaning and would still cost an embedding."""
        assert chunk_file("a.py", "\n\n   \n\t\n") == []

    def test_a_pathological_line_is_truncated_not_dropped(self) -> None:
        """One minified line should not cost a whole file its retrievability."""
        chunks = chunk_file("a.py", "short\n" + "x" * (MAX_LINE_CHARS * 3))

        assert len(chunks) == 1
        assert len(chunks[0].content.splitlines()[1]) < MAX_LINE_CHARS * 2

    def test_overlap_must_be_smaller_than_the_chunk(self) -> None:
        """Otherwise chunking cannot advance and would loop forever."""
        with pytest.raises(ValueError, match="smaller"):
            chunk_file("a.py", numbered_source(50), chunk_lines=10, overlap_lines=10)


class TestContentHash:
    def test_is_stable_for_identical_content(self) -> None:
        """The hash is what lets re-indexing skip unchanged chunks."""
        first = chunk_file("a.py", numbered_source(10))[0]
        second = chunk_file("a.py", numbered_source(10))[0]

        assert first.content_hash == second.content_hash

    def test_differs_when_content_differs(self) -> None:
        first = chunk_file("a.py", numbered_source(10))[0]
        second = chunk_file("a.py", numbered_source(11))[0]

        assert first.content_hash != second.content_hash


class TestIndexableFiles:
    @pytest.mark.parametrize(
        "path",
        ["app/main.py", "src/index.ts", "lib/thing.go", "README.md", "schema.sql"],
    )
    def test_source_is_indexable(self, path: str) -> None:
        assert is_indexable(path)

    @pytest.mark.parametrize(
        "path",
        [
            "node_modules/react/index.js",
            "vendor/lib/thing.go",
            "dist/bundle.js",
            "app/__pycache__/main.py",
            ".venv/lib/site-packages/thing.py",
            "backend/alembic/migrations/0001_x.py",
        ],
    )
    def test_generated_and_vendored_trees_are_skipped(self, path: str) -> None:
        """Embedding these costs money and pollutes every retrieval with noise."""
        assert not is_indexable(path)

    @pytest.mark.parametrize("path", ["logo.png", "package-lock.json", "data.csv", "binary.exe"])
    def test_non_source_is_skipped(self, path: str) -> None:
        assert not is_indexable(path)

    def test_dotfiles_are_skipped(self) -> None:
        assert not is_indexable(".env.example")
        assert not is_indexable("app/.hidden.py")


class TestChunkRepositoryFiles:
    def test_skips_files_that_are_not_indexable(self) -> None:
        chunks = chunk_repository_files(
            {
                "app/main.py": numbered_source(5),
                "node_modules/x/index.js": numbered_source(5),
                "logo.png": "binary-ish",
            }
        )

        assert {chunk.file_path for chunk in chunks} == {"app/main.py"}

    def test_uses_the_default_chunk_size(self) -> None:
        chunks = chunk_repository_files({"a.py": numbered_source(DEFAULT_CHUNK_LINES)})

        assert len(chunks) == 1
        assert DEFAULT_OVERLAP_LINES < DEFAULT_CHUNK_LINES


class TestMockEmbeddings:
    def test_are_deterministic(self) -> None:
        provider = MockEmbeddingProvider(128)

        assert provider.embed_query("def handler():") == provider.embed_query("def handler():")

    def test_have_the_requested_width(self) -> None:
        assert len(MockEmbeddingProvider(256).embed_query("text")) == 256

    def test_are_unit_length(self) -> None:
        """Without normalisation a long document sits far from a short query
        purely because it contains more tokens."""
        vector = MockEmbeddingProvider(128).embed_query("some words here")

        magnitude = sum(value * value for value in vector) ** 0.5
        assert magnitude == pytest.approx(1.0)

    def test_similar_text_is_closer_than_unrelated_text(self) -> None:
        """The point of feature hashing over random vectors: distances mean
        something, lexically."""
        provider = MockEmbeddingProvider(1024)

        auth = provider.embed_query("def authenticate_user(email, password): verify hash")
        login = provider.embed_query("def login(email, password): verify the password hash")
        invoice = provider.embed_query("def total_invoice(items): sum the line prices")

        def cosine(a: list[float], b: list[float]) -> float:
            return sum(x * y for x, y in zip(a, b, strict=True))

        assert cosine(auth, login) > cosine(auth, invoice)

    def test_text_with_no_tokens_yields_a_zero_vector(self) -> None:
        """Equidistant from everything, which is correct for 'this says nothing'."""
        assert MockEmbeddingProvider(64).embed_query("!!! ???") == [0.0] * 64

    def test_documents_and_queries_use_the_same_encoding(self) -> None:
        """Feature hashing has no separate query side; pretending otherwise
        would be theatre."""
        provider = MockEmbeddingProvider(64)

        assert provider.embed_documents(["text"])[0] == provider.embed_query("text")
