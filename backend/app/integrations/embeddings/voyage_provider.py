"""Voyage AI embeddings.

Anthropic does not offer an embeddings endpoint and recommends Voyage, so
DevPilot pairs Claude for reviewing with `voyage-code-3` for retrieval. That
model is trained specifically on code, which matters here: a general-purpose
text embedder treats source as prose and retrieves on comments and identifier
spelling rather than on what the code does.

Called over plain HTTP with `httpx` rather than through an SDK. The API is one
endpoint taking a list of strings, and a dependency whose entire job is to
serialise that list is not worth carrying.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.embeddings.provider import (
    EmbeddingAuthenticationError,
    EmbeddingConfigurationError,
    EmbeddingError,
    EmbeddingRateLimitError,
    EmbeddingTransientError,
)

logger = get_logger(__name__)

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"

# Voyage accepts up to 128 inputs per request. Batching is what makes indexing a
# repository affordable: one request per chunk would be both slow and costly.
MAX_BATCH_SIZE = 128

DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 30


class VoyageEmbeddingProvider:
    """Embeds text with Voyage AI."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        if settings.voyage_api_key is None:
            raise EmbeddingConfigurationError(
                "DEVPILOT_VOYAGE_API_KEY is not set. Set it, or run with "
                "DEVPILOT_EMBEDDING_MODE=mock. See docs/llm-setup.md."
            )
        self._settings = settings
        self._api_key = settings.voyage_api_key.get_secret_value()
        self._client = client or httpx.Client(timeout=settings.embedding_request_timeout_seconds)

    @property
    def model_name(self) -> str:
        return self._settings.embedding_model

    @property
    def dimensions(self) -> int:
        return self._settings.embedding_dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed stored text, in batches."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH_SIZE):
            batch = texts[start : start + MAX_BATCH_SIZE]
            vectors.extend(self._request(batch, input_type="document"))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """Embed search text.

        `input_type="query"` is not cosmetic: the model encodes queries and
        documents differently, and using the wrong side measurably degrades
        retrieval quality.
        """
        return self._request([text], input_type="query")[0]

    def _request(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        try:
            response = self._client.post(
                VOYAGE_API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "input": texts,
                    "model": self.model_name,
                    "input_type": input_type,
                    "output_dimension": self.dimensions,
                },
            )
        except httpx.TimeoutException as exc:
            raise EmbeddingTransientError("The embedding request timed out.") from exc
        except httpx.HTTPError as exc:
            raise EmbeddingTransientError(f"Could not reach the embedding provider: {exc}") from exc

        self._raise_for_status(response)

        return _extract_vectors(response.json(), expected=len(texts), dimensions=self.dimensions)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise EmbeddingRateLimitError(
                "The embedding provider rate limit was reached.",
                retry_after_seconds=(
                    int(retry_after)
                    if retry_after and retry_after.isdigit()
                    else DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
                ),
            )
        if response.status_code in (401, 403):
            raise EmbeddingAuthenticationError("The embedding provider rejected the API key.")
        if response.status_code >= 500:
            raise EmbeddingTransientError(
                f"The embedding provider returned {response.status_code}."
            )
        raise EmbeddingError(f"The embedding provider returned {response.status_code}.")


def _extract_vectors(payload: Any, *, expected: int, dimensions: int) -> list[list[float]]:
    """Pull the vectors out of a response, checking they are usable.

    A response that is short, mis-sized or out of order would corrupt the index
    silently -- chunks would be stored against the wrong embedding, and every
    later retrieval would be quietly wrong.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or len(data) != expected:
        raise EmbeddingError(
            f"The embedding provider returned {len(data) if isinstance(data, list) else 0} "
            f"vectors for {expected} inputs."
        )

    # Voyage returns an `index` on each item; sorting by it rather than trusting
    # array order keeps chunk and vector aligned whatever the provider does.
    ordered = sorted(data, key=lambda item: item.get("index", 0))

    vectors: list[list[float]] = []
    for item in ordered:
        vector = item.get("embedding")
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise EmbeddingError(
                f"Expected {dimensions}-dimensional vectors, got "
                f"{len(vector) if isinstance(vector, list) else 'none'}."
            )
        vectors.append([float(value) for value in vector])

    return vectors
