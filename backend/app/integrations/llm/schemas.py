"""The contract the LLM must satisfy.

These models are the boundary between "a language model said something" and
"DevPilot has a review". Nothing downstream sees raw model output.

Two things are validated, and they are different:

**Shape** -- Pydantic enforces the schema. Wrong types, missing fields, invented
enum values and out-of-range confidences are rejected here.

**Truth** -- the schema cannot tell whether a file path exists or a line number
falls inside the diff, and models confidently invent both. That check lives in
`app.services.llm_review`, against the actual diff, and it is the one that stops
DevPilot posting a comment on a line that does not exist.

Note what is absent: **`risk_score` is not requested from the model.** The spec
describes a response containing one, but a score the model chooses varies
between runs on identical input and cannot be justified to a user who asks why
their pull request scored 72. It is computed in Python from these severities
instead -- see `app.services.risk`. Asking for a number we would discard would
only waste tokens and invite the model to reason about the wrong thing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import FindingCategory, FindingSeverity

# Long enough for a real explanation, short enough that a runaway generation is
# rejected rather than stored.
MAX_TITLE_CHARS = 200
MAX_DESCRIPTION_CHARS = 2_000
MAX_SUGGESTION_CHARS = 2_000
MAX_SUMMARY_CHARS = 4_000

# A model asked for "everything wrong" will list dozens of trivia. Capping the
# response keeps a review readable and bounds what can be posted to a pull
# request in one go.
MAX_FINDINGS = 25


class LLMFinding(BaseModel):
    """One issue the model claims to have found."""

    # `extra="forbid"` becomes `additionalProperties: false` in the emitted JSON
    # schema, which is what makes the structured-output guarantee strict rather
    # than best-effort.
    model_config = ConfigDict(extra="forbid")

    category: FindingCategory = Field(
        description="The kind of problem: bug, security, performance or quality."
    )
    severity: FindingSeverity = Field(
        description="How much it matters. Drives the computed risk score."
    )
    file: str = Field(
        min_length=1,
        max_length=1024,
        description="Repository-relative path of the file, exactly as it appears in the diff.",
    )
    line: int | None = Field(
        default=None,
        ge=1,
        description=(
            "1-based line number in the new version of the file, or null when the "
            "finding concerns the file as a whole."
        ),
    )
    title: str = Field(
        min_length=1,
        max_length=MAX_TITLE_CHARS,
        description="A short headline, written like a code review comment.",
    )
    description: str = Field(
        min_length=1,
        max_length=MAX_DESCRIPTION_CHARS,
        description="What is wrong and why it matters.",
    )
    suggestion: str | None = Field(
        default=None,
        max_length=MAX_SUGGESTION_CHARS,
        description="How to fix it, if there is a concrete fix worth stating.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How sure you are this is a real problem. Below 0.5 means you are "
            "guessing; be honest, since low-confidence findings are not posted."
        ),
    )


class LLMReview(BaseModel):
    """A complete review, as returned by the model."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        min_length=1,
        max_length=MAX_SUMMARY_CHARS,
        description=(
            "Two or three sentences on what this pull request does and whether it "
            "looks safe to merge. Written for the author."
        ),
    )
    findings: list[LLMFinding] = Field(
        default_factory=list,
        max_length=MAX_FINDINGS,
        description="Specific problems found. An empty list is a valid, useful answer.",
    )


class LLMUsage(BaseModel):
    """Token accounting for one call, recorded so cost can be attributed."""

    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMReviewResponse(BaseModel):
    """A validated review plus the metadata needed to reproduce and cost it."""

    review: LLMReview
    model_name: str
    usage: LLMUsage = Field(default_factory=LLMUsage)
