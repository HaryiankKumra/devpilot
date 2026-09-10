"""Chooses between the real provider and the mock.

Isolated so the decision is made in exactly one place. Nothing above this layer
asks whether the model is mocked; callers receive an `LLMProvider`.
"""

from __future__ import annotations

from app.core.config import Settings
from app.integrations.llm.mock import MockReviewProvider
from app.integrations.llm.provider import LLMProvider


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Return a provider appropriate to the configured mode."""
    if settings.llm_is_mocked:
        return MockReviewProvider()

    # Imported lazily so that mock mode never constructs an SDK client, and a
    # missing key cannot fail at import time.
    from app.integrations.llm.anthropic_provider import AnthropicReviewProvider

    return AnthropicReviewProvider(settings)
