"""A deterministic stand-in for the language model.

Used when `DEVPILOT_LLM_MODE=mock`, which is the default. It lets the entire
review pipeline run to completion -- producing a stored review, a computed risk
score and persisted findings -- with no API key and no spend.

The honesty line matters here more than anywhere else in the project. This is
**not a simulated reviewer**: it does not read the diff and it has no opinion
about the code. It returns a fixed review derived from what the static analyser
already found, and its summary says so in plain words, so a review produced this
way can never be mistaken for a real one.

What it does faithfully reproduce is the *shape* of the contract: the same
Pydantic models, the same validation path, the same failure modes on request. So
everything downstream -- validation against the diff, risk scoring, persistence,
posting -- is exercised for real.
"""

from __future__ import annotations

from app.core.enums import FindingCategory, FindingSeverity
from app.core.logging import get_logger
from app.integrations.llm.provider import LLMInvalidResponseError
from app.integrations.llm.schemas import (
    LLMFinding,
    LLMReview,
    LLMReviewResponse,
    LLMUsage,
)

logger = get_logger(__name__)

MOCK_MODEL_NAME = "mock-reviewer"

# Stated in the summary of every mocked review, so nobody can mistake one for a
# genuine assessment of their code.
MOCK_SUMMARY_PREFIX = "[Mock review - no language model was called.]"

# Ruff codes mapped onto the severities a reviewer would assign them. Only used
# to give the mock something real to say; the live provider does its own
# judging.
_CODE_SEVERITY: dict[str, tuple[FindingCategory, FindingSeverity]] = {
    "F821": (FindingCategory.BUG, FindingSeverity.HIGH),
    "F401": (FindingCategory.QUALITY, FindingSeverity.LOW),
    "E722": (FindingCategory.BUG, FindingSeverity.MEDIUM),
    "S105": (FindingCategory.SECURITY, FindingSeverity.CRITICAL),
    "S106": (FindingCategory.SECURITY, FindingSeverity.CRITICAL),
    "B006": (FindingCategory.BUG, FindingSeverity.MEDIUM),
}


class MockReviewProvider:
    """Returns a fixed review built from the static-analysis findings."""

    def __init__(self, *, fail_validation: bool = False) -> None:
        # Lets tests drive the invalid-response retry path without a real model.
        self._fail_validation = fail_validation
        logger.info("llm.mock_mode_enabled")

    @property
    def model_name(self) -> str:
        return MOCK_MODEL_NAME

    def review(self, *, system_prompt: str, user_prompt: str) -> LLMReviewResponse:
        if self._fail_validation:
            raise LLMInvalidResponseError("Mock provider was configured to fail.")

        findings = _findings_from_prompt(user_prompt)

        summary = (
            f"{MOCK_SUMMARY_PREFIX} The change touches "
            f"{_count_files(user_prompt)} file(s). "
            + (
                f"Restating {len(findings)} static-analysis finding(s); no model "
                "judgement was applied."
                if findings
                else "The static analyser reported nothing, so there is nothing to restate."
            )
        )

        return LLMReviewResponse(
            review=LLMReview(summary=summary, findings=findings),
            model_name=self.model_name,
            # No tokens were spent, and recording a fabricated count would
            # corrupt any cost reporting built on this field.
            usage=LLMUsage(prompt_tokens=None, completion_tokens=None),
        )


def _count_files(user_prompt: str) -> int:
    """Count the file sections in the rendered prompt."""
    return sum(1 for line in user_prompt.splitlines() if line.startswith("### "))


def _findings_from_prompt(user_prompt: str) -> list[LLMFinding]:
    """Rebuild findings from the static-analysis lines in the prompt.

    Parsing the prompt rather than taking the findings as an argument keeps the
    mock behind exactly the same interface as the real provider: it sees only
    the two strings a model would see.
    """
    findings: list[LLMFinding] = []

    for line in user_prompt.splitlines():
        if not line.startswith("- ") or ":" not in line:
            continue

        # Rendered by StaticFinding.describe(): "- path:line CODE message"
        try:
            location, remainder = line[2:].split(" ", 1)
            path, _, line_number = location.rpartition(":")
            code, _, message = remainder.partition(" ")
            parsed_line = int(line_number)
        except (ValueError, AttributeError):
            continue

        if not path or code not in _CODE_SEVERITY:
            continue

        category, severity = _CODE_SEVERITY[code]
        findings.append(
            LLMFinding(
                category=category,
                severity=severity,
                file=path,
                line=parsed_line,
                title=f"{code}: {message}"[:200],
                description=(
                    f"The static analyser reported {code} here: {message}. "
                    "This mock provider restates analyser output verbatim and adds "
                    "no judgement of its own."
                ),
                suggestion=None,
                # Deliberately below the default posting threshold: a mocked
                # finding must never be posted to a real pull request.
                confidence=0.5,
            )
        )

    return findings
