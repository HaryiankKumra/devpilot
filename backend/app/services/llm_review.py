"""Asking the model for a review, and refusing to believe it uncritically.

Pydantic validates the *shape* of the response. This module validates its
*claims*, which is a different problem and the one that actually bites.

A model will confidently cite `src/auth/handler.py:412` for a pull request that
touched neither that file nor that line. The schema cannot catch it: the value
is a well-formed string and a positive integer. Only the diff knows the truth.
Every finding is therefore checked against the parsed diff, and anything that
does not correspond to real changed code is discarded with a logged reason.

This is what "never trust LLM output without validation" has to mean in
practice. Validating the JSON and stopping there would still let DevPilot post a
review comment on a line that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.integrations.llm.prompt import build_system_prompt, build_user_prompt
from app.integrations.llm.provider import (
    LLMInvalidResponseError,
    LLMProvider,
)
from app.integrations.llm.schemas import LLMFinding, LLMReviewResponse
from app.services.diff import ParsedDiff

if TYPE_CHECKING:
    from app.services.review_pipeline import ReviewContext

logger = get_logger(__name__)


@dataclass(frozen=True)
class RejectedFinding:
    """A finding that failed validation, and why.

    Kept rather than silently dropped: a model that regularly invents paths is
    a prompt problem, and the only way to notice is to count.
    """

    finding: LLMFinding
    reason: str


@dataclass
class ValidatedReview:
    """What survived validation."""

    summary: str
    findings: list[LLMFinding] = field(default_factory=list)
    rejected: list[RejectedFinding] = field(default_factory=list)
    model_name: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    @property
    def rejection_count(self) -> int:
        return len(self.rejected)


def request_review(
    context: ReviewContext,
    *,
    provider: LLMProvider,
    repository_full_name: str,
    pull_request_title: str,
    pull_request_number: int,
    max_retries: int = 2,
) -> ValidatedReview:
    """Ask for a review, retrying only when the response is unusable.

    An invalid response is the one failure a retry genuinely fixes: generation
    is stochastic, so the same prompt can produce conforming output next time.
    Every other error propagates for the worker to classify.
    """
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(
        context,
        repository_full_name=repository_full_name,
        pull_request_title=pull_request_title,
        pull_request_number=pull_request_number,
    )

    last_error: LLMInvalidResponseError | None = None

    for attempt in range(max_retries + 1):
        try:
            response = provider.review(system_prompt=system_prompt, user_prompt=user_prompt)
        except LLMInvalidResponseError as exc:
            last_error = exc
            logger.warning(
                "llm.invalid_response",
                attempt=attempt + 1,
                max_attempts=max_retries + 1,
                error=str(exc),
            )
            continue

        return validate_review(response, diff=context.diff)

    raise last_error or LLMInvalidResponseError("The model returned no usable review.")


def validate_review(response: LLMReviewResponse, *, diff: ParsedDiff) -> ValidatedReview:
    """Discard every finding the diff does not support."""
    kept: list[LLMFinding] = []
    rejected: list[RejectedFinding] = []

    for finding in response.review.findings:
        reason = _rejection_reason(finding, diff=diff)
        if reason is None:
            kept.append(finding)
        else:
            rejected.append(RejectedFinding(finding=finding, reason=reason))
            logger.info(
                "llm.finding_rejected",
                reason=reason,
                file=finding.file,
                line=finding.line,
            )

    if rejected:
        logger.warning(
            "llm.findings_rejected",
            kept=len(kept),
            rejected=len(rejected),
        )

    return ValidatedReview(
        summary=response.review.summary,
        findings=kept,
        rejected=rejected,
        model_name=response.model_name,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
    )


def _rejection_reason(finding: LLMFinding, *, diff: ParsedDiff) -> str | None:
    """Why this finding cannot be trusted, or `None` if it can."""
    file_diff = diff.find(finding.file)

    if file_diff is None:
        # The most common hallucination: a plausible path from elsewhere in the
        # repository, or invented outright.
        return "file is not in this pull request"

    if not file_diff.is_reviewable:
        return "file is binary or deleted"

    if finding.line is None:
        # A file-level finding. Legitimate, and cannot be posted inline.
        return None

    if finding.line not in file_diff.added_lines:
        # Either invented, or a real problem on a line the author did not
        # touch. Both are out of scope for this review.
        return "line was not added or changed by this pull request"

    return None


def postable_findings(review: ValidatedReview, *, minimum_confidence: float) -> list[LLMFinding]:
    """The findings worth posting to GitHub.

    Confidence gates posting only -- every validated finding is still stored and
    shown on the dashboard. A reviewer that posts its own guesses is one people
    stop reading, but a finding worth recording is not always worth interrupting
    someone with.
    """
    return [
        finding
        for finding in review.findings
        if finding.confidence >= minimum_confidence and finding.line is not None
    ]
