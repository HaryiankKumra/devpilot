"""Domain enumerations shared by the ORM models and the API schemas.

These live in `core` because both `db` and `schemas` need them, and the
dependency rule allows either to import `core` while forbidding `schemas` from
importing `db`.

All of them are `StrEnum`, so they serialise to readable strings in JSON and in
the database rather than to opaque integers -- a row you can read in `psql`
without a lookup table is worth the few extra bytes.
"""

from __future__ import annotations

from enum import StrEnum


class ReviewJobStatus(StrEnum):
    """Lifecycle of a single review *attempt*.

    Separate from a review's existence: a job is an attempt, which may fail and
    be retried; a review is the result, which only exists once an attempt has
    succeeded.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # Superseded by a newer push to the same pull request; there is no point
    # spending an LLM call reviewing a diff nobody will merge.
    CANCELLED = "cancelled"


class WebhookEventStatus(StrEnum):
    """What DevPilot did with a webhook delivery."""

    RECEIVED = "received"
    PROCESSED = "processed"
    # A well-formed delivery we deliberately do not act on, e.g. a PR closed
    # without merging. Recorded rather than dropped so the ledger is complete.
    IGNORED = "ignored"
    FAILED = "failed"


class PullRequestState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class FindingCategory(StrEnum):
    """What kind of problem a finding describes."""

    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    QUALITY = "quality"


class FindingSeverity(StrEnum):
    """How much a finding matters. Drives the deterministic risk score."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Weights behind the 0-100 risk score. Defined next to the severities they
# score so the two cannot drift apart, and applied in Python so the same
# findings always produce the same number -- the LLM never supplies the score.
SEVERITY_WEIGHTS: dict[FindingSeverity, int] = {
    FindingSeverity.CRITICAL: 10,
    FindingSeverity.HIGH: 7,
    FindingSeverity.MEDIUM: 4,
    FindingSeverity.LOW: 1,
}
