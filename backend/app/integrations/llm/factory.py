"""Chooses the provider for the configured mode.

Isolated so the decision is made in exactly one place. Nothing above this layer
asks which vendor is in use, or whether the model is mocked; callers receive an
`LLMProvider` and the review pipeline is identical either way.
"""

from __future__ import annotations

from app.core.config import LLMMode, Settings
from app.integrations.llm.mock import MockReviewProvider
from app.integrations.llm.provider import LLMProvider


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Return a provider appropriate to the configured mode."""
    if settings.llm_mode is LLMMode.MOCK:
        return MockReviewProvider()

    # Imported lazily so that mock mode never constructs an SDK client, a
    # missing key cannot fail at import time, and neither vendor's SDK is
    # imported when the other one is in use.
    if settings.llm_mode is LLMMode.GEMINI:
        from app.integrations.llm.gemini_provider import GeminiReviewProvider

        return GeminiReviewProvider(settings)

    from app.integrations.llm.anthropic_provider import AnthropicReviewProvider

    return AnthropicReviewProvider(settings)
