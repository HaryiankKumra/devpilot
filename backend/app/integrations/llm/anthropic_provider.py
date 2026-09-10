"""The Claude-backed review provider.

Uses `client.messages.parse()` with a Pydantic model as the output format. That
is what makes "strictly validated structured output" a property of the request
rather than something bolted on afterwards: the schema is sent with the call,
the model is constrained to it, and the SDK hands back a validated instance or
raises.

Everything here maps provider failures onto the exceptions in `provider.py`, so
the worker makes one decision -- retry or not -- without knowing which vendor
produced the error.
"""

from __future__ import annotations

from typing import Any

import anthropic

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.llm.provider import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMTransientError,
)
from app.integrations.llm.schemas import LLMReview, LLMReviewResponse, LLMUsage

logger = get_logger(__name__)

# Used when the provider rate-limits us without saying for how long.
DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 60


class AnthropicReviewProvider:
    """Produces reviews with Claude."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or self._build_client(settings)

    @staticmethod
    def _build_client(settings: Settings) -> anthropic.Anthropic:
        if settings.anthropic_api_key is None:
            raise LLMConfigurationError(
                "DEVPILOT_ANTHROPIC_API_KEY is not set. Set it, or run with "
                "DEVPILOT_LLM_MODE=mock. See docs/llm-setup.md."
            )
        return anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=settings.llm_request_timeout_seconds,
            # The worker owns retry policy, including recording each attempt on
            # the job row. Letting the SDK retry underneath would hide attempts
            # from that record and double the effective backoff.
            max_retries=0,
        )

    @property
    def model_name(self) -> str:
        return self._settings.llm_model

    def review(self, *, system_prompt: str, user_prompt: str) -> LLMReviewResponse:
        """Ask for a review and return it validated, or raise an `LLMError`."""
        try:
            response = self._client.messages.parse(
                model=self._settings.llm_model,
                max_tokens=self._settings.llm_max_output_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                output_format=LLMReview,
                # Reviewing code is exactly the kind of work adaptive thinking
                # helps with: the model reasons about the diff before answering
                # rather than pattern-matching the first suspicious line.
                thinking={"type": "adaptive"},
                output_config={"effort": self._settings.llm_effort},
            )
        except anthropic.APIConnectionError as exc:
            raise LLMTransientError(f"Could not reach the model provider: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError(
                "The model provider rate limit was reached.",
                retry_after_seconds=_retry_after_seconds(exc),
            ) from exc
        except anthropic.AuthenticationError as exc:
            raise LLMAuthenticationError("The model provider rejected the API key.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMAuthenticationError("The API key does not have access to this model.") from exc
        except anthropic.BadRequestError as exc:
            # Usually the prompt exceeded the context window. Retrying sends the
            # same oversized prompt, so this is permanent.
            raise LLMInvalidResponseError(f"The provider rejected the request: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMTransientError(f"The model provider returned {exc.status_code}.") from exc
            raise LLMInvalidResponseError(
                f"The model provider returned {exc.status_code}."
            ) from exc

        return self._to_response(response)

    def _to_response(self, response: Any) -> LLMReviewResponse:
        """Turn a provider response into a validated review."""
        # Safety classifiers can decline with HTTP 200, so the stop reason has
        # to be checked before the content is read.
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            logger.warning("llm.refused", category=category)
            raise LLMRefusalError(
                "The model declined to review this pull request"
                + (f" ({category})." if category else ".")
            )

        parsed = getattr(response, "parsed_output", None)
        if not isinstance(parsed, LLMReview):
            # The SDK validates against the schema, so this means the response
            # had no parseable output at all -- a truncated generation, usually.
            raise LLMInvalidResponseError(
                "The model did not return a review matching the required schema."
            )

        usage = getattr(response, "usage", None)
        return LLMReviewResponse(
            review=parsed,
            model_name=self.model_name,
            usage=LLMUsage(
                prompt_tokens=getattr(usage, "input_tokens", None),
                completion_tokens=getattr(usage, "output_tokens", None),
            ),
        )


def _retry_after_seconds(error: anthropic.RateLimitError) -> int:
    """How long the provider asked us to wait, if it said."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        raw = headers.get("retry-after")
        if raw and str(raw).isdigit():
            return int(raw)
    return DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
