"""Splitting source files into retrievable chunks.

Every chunk carries its file path and its start and end line. That metadata is
not decoration: without it a retrieved chunk is anonymous text the model cannot
cite, and a finding derived from it cannot be anchored to a line for posting
back to GitHub.

Chunking is done on **line boundaries with overlap**, not on a fixed character
count. A character-count split cuts through the middle of a function signature
or a string literal, and the fragment either misleads the model or is useless to
it. Splitting on lines keeps every chunk independently readable, and the overlap
means a function spanning a boundary still appears whole in one of the two
chunks that cover it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)

# Roughly a screenful of code. Large enough to hold a small function with its
# imports in view, small enough that retrieving one does not flood the prompt.
DEFAULT_CHUNK_LINES = 60

# Lines repeated between adjacent chunks, so a function that straddles a
# boundary is complete in at least one of them.
DEFAULT_OVERLAP_LINES = 10

# A single line longer than this is minified or generated. Indexing it wastes an
# embedding on something no human wrote and no reviewer will read.
MAX_LINE_CHARS = 2_000

# Extensions worth indexing. Deliberately a whitelist: a repository contains far
# more non-source than source, and embedding lockfiles, images and build output
# costs money and pollutes every retrieval with noise.
INDEXABLE_EXTENSIONS = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".scala",
        ".rb",
        ".php",
        ".cs",
        ".swift",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cc",
        ".sql",
        ".sh",
        ".bash",
        ".md",
        ".rst",
        ".yaml",
        ".yml",
        ".toml",
    }
)

# Directories that are never worth indexing, checked as path segments.
EXCLUDED_DIRECTORIES = frozenset(
    {
        "node_modules",
        "vendor",
        "dist",
        "build",
        "target",
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "migrations",
        "site-packages",
        "coverage",
        "htmlcov",
        ".next",
        ".nuxt",
    }
)


@dataclass(frozen=True)
class CodeChunkContent:
    """One indexable slice of a file, with the location metadata retrieval needs."""

    file_path: str
    start_line: int
    end_line: int
    content: str

    @property
    def content_hash(self) -> str:
        """SHA-256 of the content, used to skip re-embedding unchanged chunks.

        Embedding is the expensive part of indexing, so the cheapest possible
        way to avoid repeating it is worth having.
        """
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    def describe_location(self) -> str:
        return f"{self.file_path}:{self.start_line}-{self.end_line}"


def is_indexable(path: str) -> bool:
    """Whether a repository path is worth embedding."""
    segments = path.replace("\\", "/").split("/")

    if any(segment in EXCLUDED_DIRECTORIES for segment in segments[:-1]):
        return False

    filename = segments[-1]
    # Hidden files are configuration and dotfiles, not the source a reviewer
    # would want retrieved.
    if filename.startswith("."):
        return False

    return any(filename.endswith(extension) for extension in INDEXABLE_EXTENSIONS)


def chunk_file(
    file_path: str,
    content: str,
    *,
    chunk_lines: int = DEFAULT_CHUNK_LINES,
    overlap_lines: int = DEFAULT_OVERLAP_LINES,
) -> list[CodeChunkContent]:
    """Split one file into overlapping, line-aligned chunks.

    Line numbers are 1-based and inclusive, matching how every editor, diff and
    review comment refers to them.
    """
    if overlap_lines >= chunk_lines:
        raise ValueError(
            "overlap_lines must be smaller than chunk_lines, or chunking cannot advance"
        )

    lines = content.splitlines()
    if not lines:
        return []

    # Truncate pathological lines rather than dropping the file: a single
    # minified line should not cost a whole file its retrievability.
    lines = [
        line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + " …[truncated]"
        for line in lines
    ]

    chunks: list[CodeChunkContent] = []
    step = chunk_lines - overlap_lines
    start_index = 0

    while start_index < len(lines):
        end_index = min(start_index + chunk_lines, len(lines))
        body = "\n".join(lines[start_index:end_index])

        # Whitespace-only chunks carry no meaning and would still cost an
        # embedding call.
        if body.strip():
            chunks.append(
                CodeChunkContent(
                    file_path=file_path,
                    start_line=start_index + 1,
                    end_line=end_index,
                    content=body,
                )
            )

        if end_index >= len(lines):
            break
        start_index += step

    return chunks


def chunk_repository_files(
    files: dict[str, str],
    *,
    chunk_lines: int = DEFAULT_CHUNK_LINES,
    overlap_lines: int = DEFAULT_OVERLAP_LINES,
) -> list[CodeChunkContent]:
    """Chunk every indexable file in a mapping of path to contents."""
    chunks: list[CodeChunkContent] = []

    for path, content in sorted(files.items()):
        if not is_indexable(path):
            continue
        chunks.extend(
            chunk_file(path, content, chunk_lines=chunk_lines, overlap_lines=overlap_lines)
        )

    logger.info("chunking.completed", files=len(files), chunks=len(chunks))
    return chunks
