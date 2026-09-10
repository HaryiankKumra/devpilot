"""A deterministic embedding provider that needs no API key.

Most mock embedders return random vectors. That makes retrieval *run* while
making it meaningless -- every search returns arbitrary chunks, so nothing
downstream can be judged. This one uses **feature hashing**: each token is
hashed to a dimension and the resulting vector is L2-normalised, so texts
sharing vocabulary genuinely land near each other under cosine distance.

The honest limitation, stated plainly: **this is lexical, not semantic.** It
matches shared words. It will not connect "authenticate" to "login", which is
exactly what a real embedding model is for. It is enough to prove the retrieval
plumbing works, to develop against, and to test with -- and not enough to
substitute for the real thing in production.

Feature hashing is a real technique, not a toy: it is how `HashingVectorizer`
works. The properties that matter here are that it is deterministic, needs no
training or network, and produces vectors whose distances mean something.
"""

from __future__ import annotations

import hashlib
import math
import re

from app.core.logging import get_logger

logger = get_logger(__name__)

MOCK_EMBEDDING_MODEL = "mock-feature-hashing"

# Identifiers, words and numbers. Splitting camelCase and snake_case matters for
# code: `getUserById` should share tokens with `user` and `id`.
_TOKEN_PATTERN = re.compile(r"[A-Za-z][a-z]*|\d+")


class MockEmbeddingProvider:
    """Deterministic lexical embeddings via feature hashing."""

    def __init__(self, dimensions: int) -> None:
        self._dimensions = dimensions
        logger.info("embeddings.mock_mode_enabled", dimensions=dimensions)

    @property
    def model_name(self) -> str:
        return MOCK_EMBEDDING_MODEL

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        # Symmetric: feature hashing has no separate query encoding, and
        # pretending otherwise would be theatre.
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions

        for token in _tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self._dimensions
            # The sign bit spreads collisions in both directions, so two
            # unrelated tokens landing on one dimension tend to cancel rather
            # than reinforce.
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign

        return _normalise(vector)


def _tokenize(text: str) -> list[str]:
    """Lower-cased word and identifier fragments."""
    return [match.group(0).lower() for match in _TOKEN_PATTERN.finditer(text)]


def _normalise(vector: list[float]) -> list[float]:
    """Scale to unit length so cosine distance depends on direction, not size.

    Without this a long document would sit far from a short query purely
    because it contains more tokens.
    """
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0.0:
        # Text with no recognisable tokens. A zero vector is equidistant from
        # everything, which is the correct answer for "this says nothing".
        return vector
    return [value / magnitude for value in vector]
