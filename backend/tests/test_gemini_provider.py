"""Tests for the Gemini review provider.

No key and no network: a fake client stands in for the SDK. What is being
tested is the mapping from Gemini's failure shapes onto this project's
exception vocabulary, because that mapping is what lets the worker decide
"retry or not" without knowing which vendor produced the error.

The refusal and truncation cases matter most. Both arrive as HTTP 200, so a
provider that only inspects status codes treats a declined review as a
successful one.
"""

from __future__ import annotations

from typing import Any

import pytest
from google.genai import errors as genai_errors

from app.core.config import Environment, LLMMode, Settings
from app.integrations.llm.gemini_provider import (
    GeminiReviewProvider,
    _repair_invalid_escapes,
    _retry_after_seconds,
    schema_instructions,
)
from app.integrations.llm.key_pool import ApiKeyPool
from app.integrations.llm.provider import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMTransientError,
)
from app.integrations.llm.schemas import LLMReview

VALID_REVIEW = LLMReview(
    summary="Adds a login handler with an unvalidated redirect.",
    findings=[],
)


# --- fakes -------------------------------------------------------------------


class _Candidate:
    def __init__(self, finish_reason: str | None = "STOP") -> None:
        self.finish_reason = finish_reason


class _Usage:
    def __init__(self) -> None:
        self.prompt_token_count = 1200
        self.candidates_token_count = 340


class _Response:
    """Shaped like `GenerateContentResponse` in the parts we read."""

    def __init__(
        self,
        *,
        parsed: Any = None,
        text: str | None = None,
        finish_reason: str | None = "STOP",
        block_reason: str | None = None,
        candidates: list[_Candidate] | None = None,
    ) -> None:
        self.parsed = parsed
        self.text = text
        self.usage_metadata = _Usage()
        self.prompt_feedback = type("Feedback", (), {"block_reason": block_reason})()
        if candidates is None:
            candidates = [_Candidate(finish_reason)]
        self.candidates = candidates


class _Models:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self._outcome
        # A dict keyed by model name lets one fake answer differently per model,
        # which is what the fallback tests need.
        if isinstance(outcome, dict):
            outcome = outcome[kwargs["model"]]
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome(**kwargs)
        return outcome


class _Client:
    def __init__(self, outcome: Any, api_key: str) -> None:
        self.models = _Models(outcome)
        self.api_key = api_key


def build_provider(
    outcome: Any,
    *,
    keys: list[str] | None = None,
    fallback_models: str = ",",
) -> tuple[GeminiReviewProvider, list[_Client]]:
    """A provider wired to a fake client, plus the clients it built.

    Fallbacks default to *off* (a lone comma), so the many tests about a single
    model's failure modes are not silently rescued by a second model.
    """
    built: list[_Client] = []

    def factory(api_key: str) -> _Client:
        client = _Client(outcome, api_key)
        built.append(client)
        return client

    settings = Settings(
        _env_file=None,
        environment=Environment.CI,
        llm_mode=LLMMode.GEMINI,
        llm_fallback_models=fallback_models,
    )
    provider = GeminiReviewProvider(
        settings,
        pool=ApiKeyPool(keys or ["key-one"]),
        client_factory=factory,
    )
    return provider, built


def run(provider: GeminiReviewProvider) -> Any:
    return provider.review(system_prompt="You review code.", user_prompt="diff goes here")


def client_error(code: int, details: Any = None) -> genai_errors.ClientError:
    """A ClientError without going through the SDK's response plumbing."""
    error = genai_errors.ClientError.__new__(genai_errors.ClientError)
    Exception.__init__(error, f"HTTP {code}")
    error.code = code
    error.details = details
    return error


# --- tests -------------------------------------------------------------------


class TestConfiguration:
    def test_refuses_to_build_without_keys(self) -> None:
        settings = Settings(_env_file=None, environment=Environment.CI, llm_mode=LLMMode.GEMINI)

        with pytest.raises(LLMConfigurationError, match="DEVPILOT_GEMINI_API_KEYS"):
            GeminiReviewProvider(settings)

    def test_reports_the_configured_model(self) -> None:
        provider, _ = build_provider(_Response(parsed=VALID_REVIEW))

        assert provider.model_name == "gemini-3.6-flash"


class TestSuccess:
    def test_returns_a_validated_review(self) -> None:
        provider, _ = build_provider(_Response(parsed=VALID_REVIEW))

        result = run(provider)

        assert result.review.summary.startswith("Adds a login handler")
        assert result.model_name == "gemini-3.6-flash"

    def test_records_token_usage(self) -> None:
        """Without it, "did switching provider get cheaper" is unanswerable."""
        provider, _ = build_provider(_Response(parsed=VALID_REVIEW))

        result = run(provider)

        assert result.usage.prompt_tokens == 1200
        assert result.usage.completion_tokens == 340

    def test_asks_for_json_and_carries_the_schema_in_the_prompt(self) -> None:
        """Gemini rejects this schema in `response_schema` (see the provider
        docstring), so the shape is requested in the system instruction and the
        response is validated here instead."""
        provider, clients = build_provider(_Response(parsed=VALID_REVIEW))

        run(provider)

        config = clients[0].models.calls[0]["config"]
        assert config.response_mime_type == "application/json"
        assert config.response_schema is None
        assert "JSON Schema" in config.system_instruction
        assert config.system_instruction.startswith("You review code.")


class TestSchemaInstructions:
    """The prompt must describe exactly what the validator will accept."""

    def test_is_generated_from_the_model(self) -> None:
        """Hand-written schema text drifts from the model and produces
        validation failures nobody can explain."""
        text = schema_instructions()

        for field in ("summary", "findings", "severity", "confidence", "file"):
            assert field in text

    def test_names_the_allowed_severities(self) -> None:
        text = schema_instructions()

        for value in ("critical", "high", "medium", "low"):
            assert value in text

    def test_forbids_markdown_fences(self) -> None:
        """A fenced code block is the most common reason JSON fails to parse."""
        assert "markdown" in schema_instructions().lower()

    def test_is_compact(self) -> None:
        """It rides along with every request; pretty printing would spend a few
        hundred tokens per review on whitespace."""
        assert '": "' not in schema_instructions()

    def test_is_cached(self) -> None:
        assert schema_instructions() is schema_instructions()


class TestRejectsBadOutput:
    def test_rejects_a_dict_that_does_not_match_the_schema(self) -> None:
        provider, _ = build_provider(_Response(parsed={"nonsense": True}))

        with pytest.raises(LLMInvalidResponseError, match="does not match the schema"):
            run(provider)

    def test_rejects_text_that_is_not_json(self) -> None:
        provider, _ = build_provider(_Response(parsed=None, text="I think the code is fine!"))

        with pytest.raises(LLMInvalidResponseError):
            run(provider)

    def test_rejects_a_response_with_no_content(self) -> None:
        provider, _ = build_provider(_Response(parsed=None, text=None))

        with pytest.raises(LLMInvalidResponseError, match="no parseable review"):
            run(provider)

    def test_rejects_a_response_with_no_candidates(self) -> None:
        provider, _ = build_provider(_Response(parsed=VALID_REVIEW, candidates=[]))

        with pytest.raises(LLMInvalidResponseError, match="no candidates"):
            run(provider)


class TestRefusalAndTruncation:
    def test_a_blocked_prompt_is_a_refusal_not_a_failure(self) -> None:
        """Arrives as HTTP 200. Retrying an identical refused prompt just gets
        refused again, so it must not be classed as transient."""
        provider, _ = build_provider(_Response(parsed=None, block_reason="SAFETY"))

        with pytest.raises(LLMRefusalError):
            run(provider)

    @pytest.mark.parametrize("reason", ["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"])
    def test_a_refusing_finish_reason_is_a_refusal(self, reason: str) -> None:
        provider, _ = build_provider(_Response(parsed=VALID_REVIEW, finish_reason=reason))

        with pytest.raises(LLMRefusalError):
            run(provider)

    def test_truncation_says_so_precisely(self) -> None:
        """ "Invalid response" would send someone hunting the wrong bug; the
        cause is the token cap and the message names it."""
        provider, _ = build_provider(_Response(parsed=None, finish_reason="MAX_TOKENS"))

        with pytest.raises(LLMInvalidResponseError, match="MAX_OUTPUT_TOKENS"):
            run(provider)

    def test_an_unexpected_finish_reason_is_rejected(self) -> None:
        provider, _ = build_provider(_Response(parsed=VALID_REVIEW, finish_reason="MALFORMED"))

        with pytest.raises(LLMInvalidResponseError, match="stopped unexpectedly"):
            run(provider)

    def test_accepts_an_enum_style_finish_reason(self) -> None:
        """The SDK returns an enum whose `.name` carries the wire value."""
        enum_like = type("FinishReason", (), {"name": "STOP"})()
        provider, _ = build_provider(_Response(parsed=VALID_REVIEW, finish_reason=enum_like))

        assert run(provider).review.summary.startswith("Adds a login handler")


class TestErrorMapping:
    def test_rate_limit_cools_the_key_and_reports_the_wait(self) -> None:
        provider, _ = build_provider(client_error(429), keys=["a", "b"])

        with pytest.raises(LLMRateLimitError) as caught:
            run(provider)

        assert caught.value.retry_after_seconds == 60
        # The throttled key is out of rotation, so the next attempt uses the other.
        assert provider._pool.usable == 1

    def test_rate_limit_honours_the_providers_own_retry_delay(self) -> None:
        details = {
            "error": {
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}
                ]
            }
        }
        provider, _ = build_provider(client_error(429, details))

        with pytest.raises(LLMRateLimitError) as caught:
            run(provider)

        assert caught.value.retry_after_seconds == 17

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_key_is_disabled_not_retried(self, status: int) -> None:
        provider, _ = build_provider(client_error(status), keys=["a", "b"])

        with pytest.raises(LLMAuthenticationError):
            run(provider)

        assert provider._pool.usable == 1

    def test_a_bad_request_is_permanent(self) -> None:
        """Usually an oversized prompt: resending it unchanged fails the same."""
        provider, _ = build_provider(client_error(400))

        with pytest.raises(LLMInvalidResponseError):
            run(provider)

    def test_a_server_error_is_transient(self) -> None:
        error = genai_errors.ServerError.__new__(genai_errors.ServerError)
        Exception.__init__(error, "backend unavailable")
        error.code = 503
        provider, _ = build_provider(error)

        with pytest.raises(LLMTransientError):
            run(provider)

    def test_an_exhausted_pool_reports_as_a_rate_limit(self) -> None:
        """So the worker backs off and retries rather than failing the job: the
        quota returns on its own."""
        provider, _ = build_provider(client_error(429), keys=["only"])

        with pytest.raises(LLMRateLimitError):
            run(provider)
        with pytest.raises(LLMRateLimitError, match="rate-limited"):
            run(provider)


def server_error(code: int = 503) -> genai_errors.ServerError:
    error = genai_errors.ServerError.__new__(genai_errors.ServerError)
    Exception.__init__(error, f"HTTP {code}: high demand")
    error.code = code
    return error


class TestModelFallback:
    """A sibling model is the retry that works when one is overloaded.

    Observed on the first real pull request: gemini-3.6-flash and 3.7-flash
    both answered 503 "high demand" for minutes, six attempts with backoff all
    failed on schedule, and 3.5-flash answered in fourteen seconds.
    """

    PRIMARY = "gemini-3.6-flash"

    def test_falls_back_when_the_primary_returns_5xx(self) -> None:
        provider, clients = build_provider(
            {self.PRIMARY: server_error(503), "gemini-3.5-flash": _Response(parsed=VALID_REVIEW)},
            fallback_models="gemini-3.5-flash",
        )

        result = run(provider)

        assert [c["model"] for c in clients[0].models.calls] == [self.PRIMARY, "gemini-3.5-flash"]
        assert result.review.summary.startswith("Adds a login handler")

    def test_records_the_model_that_actually_answered(self) -> None:
        """So a fallback is visible in the data, not blended into the primary
        model's results -- "did reviews get worse?" depends on knowing."""
        provider, _ = build_provider(
            {self.PRIMARY: server_error(503), "gemini-3.5-flash": _Response(parsed=VALID_REVIEW)},
            fallback_models="gemini-3.5-flash",
        )

        assert run(provider).model_name == "gemini-3.5-flash"

    def test_does_not_fall_back_when_the_primary_succeeds(self) -> None:
        provider, clients = build_provider(
            _Response(parsed=VALID_REVIEW), fallback_models="gemini-3.5-flash"
        )

        result = run(provider)

        assert [c["model"] for c in clients[0].models.calls] == [self.PRIMARY]
        assert result.model_name == self.PRIMARY

    def test_tries_fallbacks_in_order_and_stops_at_the_first_success(self) -> None:
        provider, clients = build_provider(
            {
                self.PRIMARY: server_error(503),
                "gemini-3.7-flash": server_error(503),
                "gemini-3.5-flash": _Response(parsed=VALID_REVIEW),
            },
            fallback_models="gemini-3.7-flash,gemini-3.5-flash",
        )

        result = run(provider)

        assert [c["model"] for c in clients[0].models.calls] == [
            self.PRIMARY,
            "gemini-3.7-flash",
            "gemini-3.5-flash",
        ]
        assert result.model_name == "gemini-3.5-flash"

    def test_raises_the_last_error_when_every_model_fails(self) -> None:
        provider, _ = build_provider(
            {self.PRIMARY: server_error(503), "gemini-3.5-flash": server_error(502)},
            fallback_models="gemini-3.5-flash",
        )

        with pytest.raises(LLMTransientError, match="502"):
            run(provider)

    def test_does_not_fall_back_on_a_rate_limit(self) -> None:
        """A 429 is per key, not per model, and the pool handles it. Switching
        model would just spend the next key's quota on the same problem."""
        provider, clients = build_provider(
            {self.PRIMARY: client_error(429), "gemini-3.5-flash": _Response(parsed=VALID_REVIEW)},
            fallback_models="gemini-3.5-flash",
        )

        with pytest.raises(LLMRateLimitError):
            run(provider)
        assert [c["model"] for c in clients[0].models.calls] == [self.PRIMARY]

    def test_does_not_fall_back_on_a_refusal(self) -> None:
        """An identical refused prompt is refused by the next model too."""
        provider, clients = build_provider(
            {
                self.PRIMARY: _Response(parsed=None, block_reason="SAFETY"),
                "gemini-3.5-flash": _Response(parsed=VALID_REVIEW),
            },
            fallback_models="gemini-3.5-flash",
        )

        with pytest.raises(LLMRefusalError):
            run(provider)
        assert len(clients[0].models.calls) == 1

    def test_does_not_fall_back_on_a_rejected_key(self) -> None:
        provider, clients = build_provider(
            {self.PRIMARY: client_error(401), "gemini-3.5-flash": _Response(parsed=VALID_REVIEW)},
            fallback_models="gemini-3.5-flash",
        )

        with pytest.raises(LLMAuthenticationError):
            run(provider)
        assert len(clients[0].models.calls) == 1


class TestKeyRotation:
    def test_spreads_requests_across_keys(self) -> None:
        provider, clients = build_provider(_Response(parsed=VALID_REVIEW), keys=["a", "b", "c"])

        for _ in range(3):
            run(provider)

        assert [client.api_key for client in clients] == ["a", "b", "c"]

    def test_reuses_one_client_per_key(self) -> None:
        """Rebuilding per request would discard the connection pool and add a
        TLS handshake to every review."""
        provider, clients = build_provider(_Response(parsed=VALID_REVIEW), keys=["a", "b"])

        for _ in range(6):
            run(provider)

        assert len(clients) == 2


class TestInvalidEscapeRepair:
    """A reviewer quotes code, and code is full of backslashes.

    Observed against the real API on the very first successful call: the model
    described a regex, wrote a single backslash inside a JSON string, and the
    entire correct review became unparseable over one character.
    """

    def test_leaves_valid_json_untouched(self) -> None:
        """The repair must be a no-op for well-formed output, or it becomes a
        source of corruption rather than a fix."""
        text = r'{"a": "line\nbreak", "b": "quote\"", "c": "slash\\", "d": "\u00e9"}'

        assert _repair_invalid_escapes(text) == text

    @pytest.mark.parametrize(
        "invalid",
        [
            r'{"a": "\d+"}',
            r'{"a": "C:\Users\test"}',
            r"""{"a": "it\'s"}""",
            r'{"a": "\s"}',
            r'{"a": "\LaTeX"}',
        ],
    )
    def test_makes_invalid_escapes_parseable(self, invalid: str) -> None:
        import json as json_module

        with pytest.raises(json_module.JSONDecodeError):
            json_module.loads(invalid)

        json_module.loads(_repair_invalid_escapes(invalid))

    def test_preserves_the_backslash_as_content(self) -> None:
        """Doubling is what the model meant: a literal backslash is the only
        thing an invalid escape can have been."""
        import json as json_module

        repaired = json_module.loads(_repair_invalid_escapes(r'{"pattern": "\d{4}"}'))

        assert repaired["pattern"] == r"\d{4}"

    def test_keeps_short_unicode_escapes_repairable(self) -> None:
        r"""`\u` without four hex digits is invalid too, and must be doubled."""
        import json as json_module

        json_module.loads(_repair_invalid_escapes(r'{"a": "\u12"}'))

    def test_the_provider_recovers_a_review_with_a_stray_backslash(self) -> None:
        text = r'{"summary": "Uses the \d+ pattern unsafely.", "findings": []}'
        provider, _ = build_provider(_Response(parsed=None, text=text))

        result = run(provider)

        assert result.review.summary == r"Uses the \d+ pattern unsafely."

    def test_repair_does_not_rescue_genuinely_wrong_output(self) -> None:
        """The leniency is about JSON syntax, not about what a review may be."""
        provider, _ = build_provider(_Response(parsed=None, text='{"nonsense": "\\d"}'))

        with pytest.raises(LLMInvalidResponseError):
            run(provider)

    def test_the_error_shows_what_came_back(self) -> None:
        """Without a snippet, an unparseable response is undebuggable."""
        provider, _ = build_provider(_Response(parsed=None, text="Sorry, I cannot help."))

        with pytest.raises(LLMInvalidResponseError, match="Sorry, I cannot help"):
            run(provider)


class TestRetryAfterParsing:
    def test_defaults_when_the_provider_did_not_say(self) -> None:
        assert _retry_after_seconds(client_error(429, None)) == 60

    def test_ignores_a_malformed_delay(self) -> None:
        details = {
            "error": {
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "soon"}
                ]
            }
        }

        assert _retry_after_seconds(client_error(429, details)) == 60
