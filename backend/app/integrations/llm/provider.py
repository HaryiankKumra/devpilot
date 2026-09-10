"""The LLM provider interface and its failures.

As with the GitHub client, this is a Protocol rather than a base class, so the
mock cannot inherit real behaviour and quietly stop predicting it. Everything
above this layer depends only on `LLMProvider`, which is what lets the whole
review pipeline run with no API key.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.integrations.llm.schemas import LLMReviewResponse


class LLMError(Exception):
    """Base class for language-model failures."""


class LLMConfigurationError(LLMError):
    """No API key, or the provider is not configured.

    Raised at the point of use rather than at startup, so the rest of the
    application still runs -- and the mock provider still works -- when no key
    has been supplied.
    """


class LLMTransientError(LLMError):
    """Overload, timeout or a 5xx. Worth retrying with backoff."""


class LLMRateLimitError(LLMTransientError):
    """The provider asked us to slow down.

    Carries the wait it suggested, so the worker can back off for exactly that
    long instead of guessing.
    """

    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class LLMAuthenticationError(LLMError):
    """The key was rejected. Retrying will not help."""


class LLMInvalidResponseError(LLMError):
    """The model's output did not satisfy the schema.

    Retried a small number of times, because this is the one failure a second
    attempt genuinely fixes: generation is stochastic, so the same prompt can
    produce conforming output next time.
    """


class LLMRefusalError(LLMError):
    """The model declined to answer.

    Distinct from a malformed response: retrying an identical prompt that was
    refused just gets refused again.
    """


@runtime_checkable
class LLMProvider(Protocol):
    """What the review pipeline needs from a language model."""

    @property
    def model_name(self) -> str:
        """Identifier recorded on the review, so results stay interpretable
        after a model upgrade changes the character of the findings."""
        ...

    def review(self, *, system_prompt: str, user_prompt: str) -> LLMReviewResponse:
        """Return a schema-valid review, or raise an `LLMError`."""
        ...
