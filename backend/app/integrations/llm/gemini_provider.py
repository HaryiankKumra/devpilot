"""The Gemini-backed review provider.

Exists because Gemini has a free tier and Claude does not. Everything above this
file is unchanged: the pipeline depends on `LLMProvider`, so swapping the vendor
is a factory decision, not a rewrite. That is the entire return on having put a
Protocol there in the first place.

**Structured output, and why it is done in the prompt.** Anthropic takes
`LLMReview` as an output format and constrains generation to it. Gemini's
equivalent could not be made to accept this schema:

* `extra="forbid"` emits `additionalProperties`, which the Developer API has no
  field for and rejects outright -- `400 Unknown name "additional_properties"`.
* With that stripped, both `response_schema` and `response_json_schema` still
  fail on Gemini 3.x with a bare `400 INVALID_ARGUMENT` that names nothing.
  Trivial schemas are accepted on the same model, so the rejection is some
  feature of this schema -- enums, `anyOf` nullables and nested `$defs` are the
  candidates -- and the API will not say which.

Rather than binary-search a vendor's undocumented schema dialect and end up with
something that breaks again at the next model generation, the schema is sent as
**part of the prompt**, with `response_mime_type="application/json"` (which is
accepted everywhere) guaranteeing the response is JSON rather than prose.

That is a weaker guarantee -- the model is asked rather than constrained -- and
it is deliberately backed by the two mechanisms this project already has:
`LLMReview` validation on every response, and `llm_max_validation_retries` for
the case where generation wanders. The project's rule was never that the
provider guarantees the shape; it was that **we** check it. This makes that
explicit rather than relying on a vendor feature.

The schema text is generated from `LLMReview` rather than hand-written, so it
cannot drift from what the response is validated against.

**Keys.** A free tier is metered per key, so this provider draws from an
`ApiKeyPool` and rotates. A client is built per key and cached, because
constructing one per request would throw away connection pooling.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.llm.key_pool import (
    DEFAULT_COOL_DOWN_SECONDS,
    ApiKeyPool,
    LeasedKey,
    NoKeysAvailableError,
)
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

# Gemini reports why it stopped. Only STOP means "the answer is complete".
_FINISH_REASON_STOP = "STOP"
_FINISH_REASON_MAX_TOKENS = "MAX_TOKENS"

# Finish reasons that mean the model declined rather than failed. Retrying an
# identical prompt that was refused just gets refused again.
_REFUSAL_FINISH_REASONS = frozenset({"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"})


@lru_cache(maxsize=1)
def schema_instructions() -> str:
    """The JSON shape to ask for, generated from `LLMReview`.

    Generated rather than hand-written so it cannot drift from the model the
    response is validated against — a prompt promising one shape while the
    validator demands another is a bug that shows up only as mysterious
    validation failures.

    Serialised compactly: this rides along with every request, and pretty
    printing it would spend a few hundred tokens per review on whitespace.
    """
    schema = json.dumps(LLMReview.model_json_schema(), separators=(",", ":"))
    return (
        "\n\nReturn a single JSON object, and nothing else — no prose, no "
        "markdown fences. It must validate against this JSON Schema:\n\n"
        f"{schema}\n\n"
        "`findings` may be an empty list. Use null for `line` and `suggestion` "
        "when they do not apply, rather than omitting them or inventing values."
    )


class GeminiReviewProvider:
    """Produces reviews with Google Gemini, rotating across a pool of keys."""

    def __init__(
        self,
        settings: Settings,
        *,
        pool: ApiKeyPool | None = None,
        client_factory: Any | None = None,
    ) -> None:
        self._settings = settings
        self._pool = pool or self._build_pool(settings)
        # Injectable so tests never construct a real SDK client.
        self._client_factory = client_factory or self._default_client_factory
        self._clients: dict[str, Any] = {}

    @staticmethod
    def _build_pool(settings: Settings) -> ApiKeyPool:
        keys = settings.gemini_api_key_list
        if not keys:
            raise LLMConfigurationError(
                "DEVPILOT_GEMINI_API_KEYS is not set. Set one or more comma-separated "
                "keys, or run with DEVPILOT_LLM_MODE=mock. See docs/llm-setup.md."
            )
        return ApiKeyPool(keys)

    def _default_client_factory(self, api_key: str) -> genai.Client:
        return genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(
                timeout=int(self._settings.llm_request_timeout_seconds * 1000),
            ),
        )

    def _client_for(self, leased: LeasedKey) -> Any:
        """One client per key, reused. Rebuilding per request would discard the
        connection pool and add a TLS handshake to every review."""
        client = self._clients.get(leased.label)
        if client is None:
            client = self._client_factory(leased.secret)
            self._clients[leased.label] = client
        return client

    @property
    def model_name(self) -> str:
        return self._settings.llm_model

    def review(self, *, system_prompt: str, user_prompt: str) -> LLMReviewResponse:
        """Ask for a review and return it validated, or raise an `LLMError`.

        Tries the primary model, then each fallback in turn -- but only when the
        failure is a server-side 5xx. On the free tier a flash model can answer
        "high demand, try again later" for minutes at a stretch, and retrying
        that same model with backoff just fails on schedule; a sibling model is
        the retry that actually works. Every other failure is raised at once:
        a rate limit is per key and handled by the pool, a refusal or a bad
        response would recur on any model, and a rejected key is not a model
        problem at all.
        """
        try:
            leased = self._pool.acquire()
        except NoKeysAvailableError as exc:
            # Surfaced as a rate limit rather than a configuration error: the
            # worker already knows how to back off and retry that.
            raise LLMRateLimitError(str(exc), retry_after_seconds=exc.retry_after_seconds) from exc

        client = self._client_for(leased)
        candidates = [self._settings.llm_model, *self._settings.llm_fallback_model_list]
        last_error: LLMTransientError | None = None

        for index, model in enumerate(candidates):
            try:
                response = self._generate(client, leased, model, system_prompt, user_prompt)
            except LLMRateLimitError:
                # A subclass of LLMTransientError, so it must be caught first.
                # It is per key, not per model, and the pool has already parked
                # the key; switching model would spend the next key on the same
                # problem.
                raise
            except LLMTransientError as exc:
                last_error = exc
                remaining = candidates[index + 1 :]
                if remaining:
                    logger.warning(
                        "llm.model_fallback",
                        provider="gemini",
                        failed_model=model,
                        next_model=remaining[0],
                        error=str(exc)[:160],
                    )
                continue

            if index > 0:
                # Recorded on the review row as the model that answered, so a
                # fallback is visible in the data rather than silently blended
                # into the primary model's results.
                logger.info("llm.model_fallback_succeeded", provider="gemini", model=model)
            return self._to_response(response, model=model)

        assert last_error is not None  # every candidate raised, so one was caught
        raise last_error

    def _generate(
        self,
        client: Any,
        leased: LeasedKey,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> Any:
        """One request to one model, with failures mapped onto `LLMError`s."""
        try:
            return client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=genai_types.GenerateContentConfig(
                    # The schema rides in the prompt rather than in
                    # `response_schema`; see the module docstring for why.
                    system_instruction=system_prompt + schema_instructions(),
                    max_output_tokens=self._settings.llm_max_output_tokens,
                    # Still worth setting without a schema: it stops the model
                    # wrapping its JSON in prose or a markdown fence, which is
                    # the most common way a parse fails.
                    response_mime_type="application/json",
                    # Deterministic-ish: reviewing code rewards consistency far
                    # more than variety, and a lower temperature also makes a
                    # validation retry meaningfully likely to differ.
                    temperature=self._settings.llm_temperature,
                ),
            )
        except genai_errors.ClientError as exc:
            self._handle_client_error(exc, leased)
            raise  # unreachable; _handle_client_error always raises
        except genai_errors.ServerError as exc:
            raise LLMTransientError(f"Gemini returned a server error: {exc}") from exc
        except genai_errors.APIError as exc:
            # Connection-level problems surface here rather than as a status.
            raise LLMTransientError(f"Could not reach Gemini: {exc}") from exc

    def _handle_client_error(self, exc: genai_errors.ClientError, leased: LeasedKey) -> None:
        """Map a 4xx onto this project's exception vocabulary. Always raises."""
        status = getattr(exc, "code", None)

        if status == 429:
            wait = _retry_after_seconds(exc)
            self._pool.cool_down(leased, wait)
            raise LLMRateLimitError(
                "The Gemini free-tier quota for this key is exhausted.",
                retry_after_seconds=wait,
            ) from exc

        if status in (401, 403):
            # This key will not start working. Take it out of rotation so a
            # single bad key does not become a permanent one-in-N failure.
            self._pool.disable(leased, reason=f"HTTP {status}")
            raise LLMAuthenticationError("Gemini rejected the API key.") from exc

        # 400 is usually a prompt over the context window; resending it
        # unchanged fails identically, so this is permanent, not transient.
        raise LLMInvalidResponseError(f"Gemini rejected the request: {exc}") from exc

    def _to_response(self, response: Any, *, model: str) -> LLMReviewResponse:
        """Turn a Gemini response into a validated review."""
        self._reject_if_declined(response)

        parsed = getattr(response, "parsed", None)

        # Re-validate rather than trust `parsed`. The SDK may hand back a dict
        # when it cannot instantiate the model, and a review that skipped
        # validation is exactly what this project refuses to build on.
        if isinstance(parsed, LLMReview):
            return self._wrap(parsed, response, model=model)

        if isinstance(parsed, dict):
            return self._wrap(self._validate(parsed), response, model=model)

        text = getattr(response, "text", None)
        if text:
            return self._wrap(self._validate_json(text), response, model=model)

        raise LLMInvalidResponseError("Gemini returned no parseable review content.")

    def _reject_if_declined(self, response: Any) -> None:
        """Distinguish a refusal and a truncation from a usable answer.

        Both arrive as HTTP 200, so neither shows up as an error unless the
        finish reason is read.
        """
        feedback = getattr(response, "prompt_feedback", None)
        blocked = getattr(feedback, "block_reason", None)
        if blocked:
            logger.warning("llm.refused", provider="gemini", reason=str(blocked))
            raise LLMRefusalError(f"Gemini declined to review this pull request ({blocked}).")

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise LLMInvalidResponseError("Gemini returned no candidates.")

        reason = _finish_reason(candidates[0])
        if reason in _REFUSAL_FINISH_REASONS:
            logger.warning("llm.refused", provider="gemini", reason=reason)
            raise LLMRefusalError(f"Gemini declined to review this pull request ({reason}).")

        if reason == _FINISH_REASON_MAX_TOKENS:
            # The JSON is truncated, so it cannot parse. Say so precisely --
            # "invalid response" would send someone hunting the wrong bug.
            raise LLMInvalidResponseError(
                "Gemini hit the output token limit and the review was truncated. "
                "Raise DEVPILOT_LLM_MAX_OUTPUT_TOKENS or review a smaller diff."
            )

        if reason and reason != _FINISH_REASON_STOP:
            raise LLMInvalidResponseError(f"Gemini stopped unexpectedly ({reason}).")

    @staticmethod
    def _validate(payload: dict[str, Any]) -> LLMReview:
        try:
            return LLMReview.model_validate(payload)
        except Exception as exc:  # pydantic.ValidationError, but keep it broad
            raise LLMInvalidResponseError(
                f"Gemini returned a review that does not match the schema: {exc}"
            ) from exc

    @staticmethod
    def _validate_json(text: str) -> LLMReview:
        try:
            return LLMReview.model_validate_json(text)
        except Exception as first_error:  # pydantic.ValidationError, but keep it broad
            repaired = _repair_invalid_escapes(text)
            if repaired != text:
                try:
                    review = LLMReview.model_validate_json(repaired)
                except Exception:
                    pass  # Fall through to the original, more informative error.
                else:
                    # Worth a log line: frequent repairs mean the prompt should
                    # be telling the model to escape backslashes, not this.
                    logger.warning("llm.json_repaired", provider="gemini", repair="escapes")
                    return review

            raise LLMInvalidResponseError(
                f"Gemini returned output that is not a valid review: {first_error}. "
                f"First 200 characters: {text[:200]!r}"
            ) from first_error

    def _wrap(self, review: LLMReview, response: Any, *, model: str) -> LLMReviewResponse:
        usage = getattr(response, "usage_metadata", None)
        return LLMReviewResponse(
            review=review,
            model_name=model,
            usage=LLMUsage(
                prompt_tokens=getattr(usage, "prompt_token_count", None),
                completion_tokens=getattr(usage, "candidates_token_count", None),
            ),
        )


# JSON permits exactly these escapes. Anything else after a backslash makes the
# whole document unparseable.
_INVALID_ESCAPE = re.compile(r'\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')


def _repair_invalid_escapes(text: str) -> str:
    """Escape stray backslashes so a nearly-valid JSON document parses.

    A reviewer quotes code constantly, and code is full of backslashes --
    regexes (`\\d`), Windows paths, LaTeX. Models routinely copy one into a JSON
    string without doubling it, which invalidates the entire response: a
    complete, correct review is thrown away over one character.

    Doubling the backslash is what the model meant in every case where this
    fires, because a literal backslash is the only thing an invalid escape can
    have been. This runs **only after a clean parse has already failed**, so
    well-formed output is never touched, and the result is still validated
    against `LLMReview` afterwards -- the leniency is about JSON syntax, not
    about what a review is allowed to contain.
    """
    return _INVALID_ESCAPE.sub(r"\\\\", text)


def _finish_reason(candidate: Any) -> str | None:
    """The candidate's finish reason as a plain string, whatever its type."""
    reason = getattr(candidate, "finish_reason", None)
    if reason is None:
        return None
    # The SDK returns an enum whose `.name` is the wire value; older shapes
    # return the string directly.
    return str(getattr(reason, "name", reason))


def _retry_after_seconds(error: genai_errors.ClientError) -> int:
    """How long Gemini asked us to wait, if it said.

    Gemini reports this inside the error payload as a `RetryInfo` detail rather
    than in a `Retry-After` header, so it takes some digging.
    """
    details = getattr(error, "details", None)
    if isinstance(details, dict):
        entries = details.get("error", {}).get("details", [])
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            if "RetryInfo" not in str(entry.get("@type", "")):
                continue
            delay = str(entry.get("retryDelay", ""))
            # Formatted as a protobuf duration, e.g. "17s".
            if delay.endswith("s") and delay[:-1].replace(".", "", 1).isdigit():
                return max(1, int(float(delay[:-1])))

    return DEFAULT_COOL_DOWN_SECONDS
