"""Tests for LLM response validation.

Pydantic already guarantees the response's shape. What these cover is the part
a schema cannot: whether the model's *claims* survive contact with the diff.
A hallucinated file path and a hallucinated line number are both well-formed
values, and both would otherwise become a review comment pointing at nothing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.enums import FindingCategory, FindingSeverity
from app.integrations.llm.provider import LLMInvalidResponseError
from app.integrations.llm.schemas import (
    MAX_FINDINGS,
    LLMFinding,
    LLMReview,
    LLMReviewResponse,
    LLMUsage,
)
from app.services.diff import parse_unified_diff
from app.services.llm_review import (
    postable_findings,
    request_review,
    validate_review,
)
from app.services.review_pipeline import ReviewContext

DIFF = """diff --git a/checkout/coupons.py b/checkout/coupons.py
--- a/checkout/coupons.py
+++ b/checkout/coupons.py
@@ -1,4 +1,7 @@
 import json
+import os

 def apply(order):
+    discount = LOOKUP[order]
+    return discount
"""

# Added lines are 2, 5 and 6.
PARSED = parse_unified_diff(DIFF)


def make_finding(**overrides: object) -> LLMFinding:
    defaults: dict[str, object] = {
        "category": FindingCategory.BUG,
        "severity": FindingSeverity.HIGH,
        "file": "checkout/coupons.py",
        "line": 5,
        "title": "Undefined name",
        "description": "LOOKUP is never defined in this module.",
        "confidence": 0.9,
    }
    return LLMFinding(**{**defaults, **overrides})


def make_response(*findings: LLMFinding, summary: str = "A summary.") -> LLMReviewResponse:
    return LLMReviewResponse(
        review=LLMReview(summary=summary, findings=list(findings)),
        model_name="test-model",
        usage=LLMUsage(prompt_tokens=100, completion_tokens=50),
    )


class TestSchemaValidation:
    """The shape checks Pydantic performs before anything else sees the data."""

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_rejects_confidence_outside_zero_to_one(self, confidence: float) -> None:
        with pytest.raises(ValidationError):
            make_finding(confidence=confidence)

    def test_rejects_a_zero_or_negative_line(self) -> None:
        with pytest.raises(ValidationError):
            make_finding(line=0)

    def test_rejects_an_invented_severity(self) -> None:
        with pytest.raises(ValidationError):
            make_finding(severity="catastrophic")

    def test_rejects_an_invented_category(self) -> None:
        with pytest.raises(ValidationError):
            make_finding(category="vibes")

    def test_rejects_unknown_fields(self) -> None:
        """`extra="forbid"` becomes `additionalProperties: false` in the schema
        sent to the model, which is what makes the constraint strict."""
        with pytest.raises(ValidationError):
            LLMFinding(
                category=FindingCategory.BUG,
                severity=FindingSeverity.LOW,
                file="a.py",
                line=1,
                title="t",
                description="d",
                confidence=0.5,
                risk_score=99,  # type: ignore[call-arg]
            )

    def test_rejects_an_empty_summary(self) -> None:
        with pytest.raises(ValidationError):
            LLMReview(summary="", findings=[])

    def test_caps_the_number_of_findings(self) -> None:
        """A model asked for everything wrong will list dozens of trivia."""
        with pytest.raises(ValidationError):
            LLMReview(summary="s", findings=[make_finding()] * (MAX_FINDINGS + 1))

    def test_an_empty_finding_list_is_valid(self) -> None:
        """A clean pull request is a legitimate answer."""
        assert LLMReview(summary="Looks fine.", findings=[]).findings == []

    def test_line_may_be_null_for_a_file_level_finding(self) -> None:
        assert make_finding(line=None).line is None


class TestHallucinatedFiles:
    def test_a_file_not_in_the_diff_is_rejected(self) -> None:
        """The most common hallucination: a plausible path from elsewhere in the
        repository, or invented outright."""
        result = validate_review(
            make_response(make_finding(file="src/auth/handler.py", line=5)), diff=PARSED
        )

        assert result.findings == []
        assert result.rejection_count == 1
        assert "not in this pull request" in result.rejected[0].reason

    def test_a_near_miss_path_is_still_rejected(self) -> None:
        result = validate_review(
            make_response(make_finding(file="checkout/coupon.py")), diff=PARSED
        )

        assert result.findings == []


class TestHallucinatedLines:
    def test_a_line_outside_the_diff_is_rejected(self) -> None:
        """Line 412 of a seven-line file is not a review comment anyone wants."""
        result = validate_review(make_response(make_finding(line=412)), diff=PARSED)

        assert result.findings == []
        assert "not added or changed" in result.rejected[0].reason

    def test_an_untouched_line_inside_the_file_is_rejected(self) -> None:
        """Line 1 exists but the author did not write it in this change."""
        result = validate_review(make_response(make_finding(line=1)), diff=PARSED)

        assert result.findings == []

    @pytest.mark.parametrize("line", [2, 5, 6])
    def test_a_genuinely_changed_line_is_kept(self, line: int) -> None:
        result = validate_review(make_response(make_finding(line=line)), diff=PARSED)

        assert len(result.findings) == 1
        assert result.rejection_count == 0

    def test_a_file_level_finding_is_kept(self) -> None:
        """Some problems concern the file as a whole and have no line."""
        result = validate_review(make_response(make_finding(line=None)), diff=PARSED)

        assert len(result.findings) == 1


class TestMixedResponses:
    def test_good_findings_survive_alongside_bad_ones(self) -> None:
        """One hallucination must not discard the whole review."""
        result = validate_review(
            make_response(
                make_finding(line=5),
                make_finding(file="invented.py", line=1),
                make_finding(line=6),
                make_finding(line=999),
            ),
            diff=PARSED,
        )

        assert len(result.findings) == 2
        assert result.rejection_count == 2

    def test_the_summary_is_always_preserved(self) -> None:
        """The summary describes the change, not a specific line, so it stands
        even when every finding is discarded."""
        result = validate_review(
            make_response(make_finding(file="nope.py"), summary="Overall assessment."),
            diff=PARSED,
        )

        assert result.summary == "Overall assessment."
        assert result.findings == []

    def test_usage_is_carried_through(self) -> None:
        result = validate_review(make_response(make_finding()), diff=PARSED)

        assert result.prompt_tokens == 100
        assert result.completion_tokens == 50
        assert result.model_name == "test-model"


class TestPostableFindings:
    def test_low_confidence_findings_are_not_posted(self) -> None:
        """A reviewer that posts its own guesses is one people stop reading."""
        result = validate_review(
            make_response(make_finding(confidence=0.4), make_finding(confidence=0.95)),
            diff=PARSED,
        )

        postable = postable_findings(result, minimum_confidence=0.7)

        assert len(postable) == 1
        assert postable[0].confidence == 0.95

    def test_low_confidence_findings_are_still_stored(self) -> None:
        """Confidence gates posting, not recording -- the dashboard shows both."""
        result = validate_review(make_response(make_finding(confidence=0.1)), diff=PARSED)

        assert len(result.findings) == 1
        assert postable_findings(result, minimum_confidence=0.7) == []

    def test_file_level_findings_are_not_posted_inline(self) -> None:
        """There is no line to attach an inline comment to."""
        result = validate_review(
            make_response(make_finding(line=None, confidence=1.0)), diff=PARSED
        )

        assert postable_findings(result, minimum_confidence=0.7) == []


class TestRetryOnInvalidResponse:
    def test_retries_then_succeeds(self) -> None:
        """Generation is stochastic, so a second attempt genuinely can work."""
        provider = _FlakyProvider(failures=2)

        result = request_review(
            _context(),
            provider=provider,
            repository_full_name="o/r",
            pull_request_title="t",
            pull_request_number=1,
            max_retries=2,
        )

        assert provider.calls == 3
        assert result.summary == "Recovered."

    def test_gives_up_once_the_budget_is_spent(self) -> None:
        provider = _FlakyProvider(failures=99)

        with pytest.raises(LLMInvalidResponseError):
            request_review(
                _context(),
                provider=provider,
                repository_full_name="o/r",
                pull_request_title="t",
                pull_request_number=1,
                max_retries=2,
            )

        assert provider.calls == 3

    def test_a_valid_first_response_is_not_retried(self) -> None:
        provider = _FlakyProvider(failures=0)

        request_review(
            _context(),
            provider=provider,
            repository_full_name="o/r",
            pull_request_title="t",
            pull_request_number=1,
            max_retries=2,
        )

        assert provider.calls == 1


def _context() -> ReviewContext:
    return ReviewContext(diff=PARSED)


class _FlakyProvider:
    """Fails with an invalid response a set number of times, then succeeds."""

    def __init__(self, *, failures: int) -> None:
        self._remaining_failures = failures
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "flaky"

    def review(self, *, system_prompt: str, user_prompt: str) -> LLMReviewResponse:
        self.calls += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise LLMInvalidResponseError("malformed")
        return make_response(summary="Recovered.")
