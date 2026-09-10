"""Deterministic risk scoring.

The score is computed here, in Python, from validated findings. The model is
never asked for it.

That is the single most important decision in this file. A model asked to rate
risk on a 0-100 scale gives a plausible number that varies between runs on
byte-identical input, cannot be explained to a user who asks why their pull
request scored 72, and cannot be unit tested. Deriving it from severities makes
it reproducible, auditable and tunable without touching a prompt.

The scoring has two parts, and the second exists because the first is not
enough on its own:

**A weighted sum**, so that more problems mean more risk.

**A severity floor**, so that one critical finding cannot be diluted by a
quiet diff. Under a pure sum, a single critical issue (10 points) would score
lower than eleven cosmetic ones (11 points), which inverts the thing the score
is for. The floor guarantees that the worst finding alone puts the score in the
band it belongs in.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.core.enums import SEVERITY_WEIGHTS, FindingSeverity

MIN_RISK_SCORE = 0
MAX_RISK_SCORE = 100

# Converts the weighted sum into the 0-100 range. Chosen so that two critical
# findings (2 x 10 x 5) saturate the scale: at that point the pull request needs
# human attention regardless of what else is in it, and further precision at the
# top of the range would be false precision.
WEIGHT_SCALE = 5

# The minimum score implied by the single worst finding present, regardless of
# how few findings there are. These are the band boundaries a reader intuits
# from a number: 75+ reads as "do not merge", 50+ as "look carefully".
SEVERITY_FLOORS: dict[FindingSeverity, int] = {
    FindingSeverity.CRITICAL: 75,
    FindingSeverity.HIGH: 50,
    FindingSeverity.MEDIUM: 25,
    FindingSeverity.LOW: 5,
}

# Ordered worst-first, so "the highest severity present" is a scan rather than a
# comparison function.
SEVERITY_ORDER: Sequence[FindingSeverity] = (
    FindingSeverity.CRITICAL,
    FindingSeverity.HIGH,
    FindingSeverity.MEDIUM,
    FindingSeverity.LOW,
)


def calculate_risk_score(severities: Iterable[FindingSeverity]) -> int:
    """Return a 0-100 risk score for a set of finding severities.

    Pure and total: the same severities always produce the same number, and no
    input produces an error. An empty review scores 0 -- nothing was found, so
    there is nothing to be worried about.
    """
    present = list(severities)
    if not present:
        return MIN_RISK_SCORE

    weighted_sum = sum(SEVERITY_WEIGHTS[severity] for severity in present)
    scaled = weighted_sum * WEIGHT_SCALE

    floor = _highest_severity_floor(present)

    return max(MIN_RISK_SCORE, min(MAX_RISK_SCORE, max(scaled, floor)))


def _highest_severity_floor(severities: Sequence[FindingSeverity]) -> int:
    """The floor implied by the worst finding present."""
    for severity in SEVERITY_ORDER:
        if severity in severities:
            return SEVERITY_FLOORS[severity]
    return MIN_RISK_SCORE


def describe_risk(score: int) -> str:
    """A short label for a score, for display alongside the number.

    The bands match `SEVERITY_FLOORS`, so a review containing one critical
    finding is always described as `critical` -- the words and the number never
    disagree.
    """
    if score >= SEVERITY_FLOORS[FindingSeverity.CRITICAL]:
        return "critical"
    if score >= SEVERITY_FLOORS[FindingSeverity.HIGH]:
        return "high"
    if score >= SEVERITY_FLOORS[FindingSeverity.MEDIUM]:
        return "medium"
    if score > MIN_RISK_SCORE:
        return "low"
    return "none"
