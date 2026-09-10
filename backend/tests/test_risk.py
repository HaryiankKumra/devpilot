"""Tests for deterministic risk scoring.

The whole point of computing the score in Python is that it is reproducible and
explainable, so these tests assert both the arithmetic and the properties a user
would expect the number to have.
"""

from __future__ import annotations

import pytest

from app.core.enums import SEVERITY_WEIGHTS, FindingSeverity
from app.services.risk import (
    MAX_RISK_SCORE,
    MIN_RISK_SCORE,
    SEVERITY_FLOORS,
    calculate_risk_score,
    describe_risk,
)

CRITICAL = FindingSeverity.CRITICAL
HIGH = FindingSeverity.HIGH
MEDIUM = FindingSeverity.MEDIUM
LOW = FindingSeverity.LOW


class TestWeights:
    def test_match_the_specified_values(self) -> None:
        assert SEVERITY_WEIGHTS[CRITICAL] == 10
        assert SEVERITY_WEIGHTS[HIGH] == 7
        assert SEVERITY_WEIGHTS[MEDIUM] == 4
        assert SEVERITY_WEIGHTS[LOW] == 1

    def test_are_strictly_ordered(self) -> None:
        """A more severe finding must always weigh more than a less severe one."""
        weights = [SEVERITY_WEIGHTS[s] for s in (CRITICAL, HIGH, MEDIUM, LOW)]

        assert weights == sorted(weights, reverse=True)
        assert len(set(weights)) == len(weights)


class TestEmptyReview:
    def test_no_findings_scores_zero(self) -> None:
        assert calculate_risk_score([]) == MIN_RISK_SCORE


class TestBounds:
    def test_never_exceeds_one_hundred(self) -> None:
        assert calculate_risk_score([CRITICAL] * 50) == MAX_RISK_SCORE

    def test_never_falls_below_zero(self) -> None:
        assert calculate_risk_score([LOW]) >= MIN_RISK_SCORE

    @pytest.mark.parametrize(
        "severities",
        [
            [LOW],
            [MEDIUM, LOW],
            [HIGH, MEDIUM, LOW],
            [CRITICAL, HIGH, MEDIUM, LOW],
            [LOW] * 200,
        ],
    )
    def test_always_lands_in_range(self, severities: list[FindingSeverity]) -> None:
        assert MIN_RISK_SCORE <= calculate_risk_score(severities) <= MAX_RISK_SCORE


class TestSeverityFloor:
    def test_one_critical_finding_scores_at_least_seventy_five(self) -> None:
        """The reason the floor exists: a single critical issue is a high-risk
        pull request however quiet the rest of the diff is."""
        assert calculate_risk_score([CRITICAL]) >= SEVERITY_FLOORS[CRITICAL]

    def test_a_critical_outranks_many_trivial_findings(self) -> None:
        """Under a pure weighted sum, eleven lows (11) would beat one critical
        (10) -- inverting the thing the score is for."""
        one_critical = calculate_risk_score([CRITICAL])
        eleven_lows = calculate_risk_score([LOW] * 11)

        assert one_critical > eleven_lows

    @pytest.mark.parametrize(
        ("severity", "floor"),
        [(CRITICAL, 75), (HIGH, 50), (MEDIUM, 25), (LOW, 5)],
    )
    def test_each_severity_has_its_floor(self, severity: FindingSeverity, floor: int) -> None:
        assert calculate_risk_score([severity]) >= floor

    def test_the_floor_comes_from_the_worst_finding_present(self) -> None:
        """A critical buried among lows still sets the floor."""
        assert calculate_risk_score([LOW, LOW, CRITICAL, LOW]) >= SEVERITY_FLOORS[CRITICAL]


class TestMonotonicity:
    def test_adding_a_finding_never_lowers_the_score(self) -> None:
        running: list[FindingSeverity] = []
        previous = calculate_risk_score(running)

        for severity in [LOW, MEDIUM, LOW, HIGH, LOW, CRITICAL]:
            running.append(severity)
            current = calculate_risk_score(running)
            assert current >= previous
            previous = current

    def test_a_worse_finding_scores_at_least_as_high(self) -> None:
        assert calculate_risk_score([CRITICAL]) >= calculate_risk_score([HIGH])
        assert calculate_risk_score([HIGH]) >= calculate_risk_score([MEDIUM])
        assert calculate_risk_score([MEDIUM]) >= calculate_risk_score([LOW])


class TestDeterminism:
    def test_the_same_findings_always_produce_the_same_score(self) -> None:
        """The reason the model is not asked for this number."""
        severities = [CRITICAL, MEDIUM, LOW, HIGH]

        assert len({calculate_risk_score(severities) for _ in range(100)}) == 1

    def test_order_does_not_matter(self) -> None:
        """Findings arrive in whatever order the model listed them."""
        assert calculate_risk_score([LOW, CRITICAL, MEDIUM]) == calculate_risk_score(
            [CRITICAL, MEDIUM, LOW]
        )


class TestDescribeRisk:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0, "none"),
            (1, "low"),
            (24, "low"),
            (25, "medium"),
            (49, "medium"),
            (50, "high"),
            (74, "high"),
            (75, "critical"),
            (100, "critical"),
        ],
    )
    def test_bands(self, score: int, expected: str) -> None:
        assert describe_risk(score) == expected

    @pytest.mark.parametrize("severity", [CRITICAL, HIGH, MEDIUM, LOW])
    def test_the_label_never_understates_the_worst_finding(self, severity: FindingSeverity) -> None:
        """The words and the number must not disagree: a review containing a
        critical finding must not be described as merely 'medium'."""
        label = describe_risk(calculate_risk_score([severity]))

        assert label == severity.value
