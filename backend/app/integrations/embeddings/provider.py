"""The embedding provider interface.

Embeddings are a separate concern from the review model, and a separate vendor:
Anthropic does not offer an embeddings endpoint, so the live implementation uses
Voyage AI (the provider Anthropic recommends), while reviews go to Claude.

Keeping this behind its own Protocol means the two can be swapped
independently -- and, more usefully, that the mock can produce vectors that are
genuinely comparable to each other without any API key at all.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class EmbeddingError(Exception):
    """Base class for embedding failures."""


class EmbeddingConfigurationError(EmbeddingError):
    """No API key, or the provider is not configured."""


class EmbeddingTransientError(EmbeddingError):
    """Overload, timeout or 5xx. Worth retrying."""


class EmbeddingRateLimitError(EmbeddingTransientError):
    """The provider asked us to slow down."""

    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class EmbeddingAuthenticationError(EmbeddingError):
    """The key was rejected. Retrying will not help."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors that can be compared by distance."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int:
        """Vector width. Must match the `code_chunks.embedding` column exactly:
        pgvector fixes dimensionality at the column, and comparing vectors from
        different models is meaningless even when the widths happen to agree."""
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed text that will be *stored* and searched over."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed text used to *search*.

        Separate from `embed_documents` because retrieval models are often
        asymmetric: they encode a query and a document differently, and using
        the wrong side measurably degrades results.
        """
        ...
