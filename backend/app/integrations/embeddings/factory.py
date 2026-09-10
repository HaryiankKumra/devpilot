"""Chooses between the real embedding provider and the mock."""

from __future__ import annotations

from app.core.config import Settings
from app.integrations.embeddings.mock import MockEmbeddingProvider
from app.integrations.embeddings.provider import EmbeddingProvider


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Return a provider appropriate to the configured mode."""
    if settings.embedding_is_mocked:
        return MockEmbeddingProvider(settings.embedding_dimensions)

    # Imported lazily so mock mode never needs the HTTP client or a key.
    from app.integrations.embeddings.voyage_provider import VoyageEmbeddingProvider

    return VoyageEmbeddingProvider(settings)
