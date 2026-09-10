"""Tests for the static analysis layer.

The case that matters most here is the *absent analyser*. Reporting zero
findings when the tool is missing is indistinguishable from reporting clean
code, so it must fail loudly instead.
"""

from __future__ import annotations

import pytest

from app.services.diff import ChangeType, FileDiff
from app.services.static_analysis import (
    RUFF_RULES,
    AnalyzerUnavailableError,
    RuffAnalyzer,
    analyze_file,
)

PROBLEM_SOURCE = """import os


def handler(value):
    try:
        return LOOKUP[value]
    except:
        return None
"""


def make_diff(path: str = "app/thing.py", lines: set[int] | None = None) -> FileDiff:
    return FileDiff(
        path=path,
        change_type=ChangeType.MODIFIED,
        added_lines=lines if lines is not None else set(range(1, 20)),
    )


class TestRuffAnalyzer:
    def test_reports_it_is_available(self) -> None:
        assert RuffAnalyzer.is_available() is True

    def test_finds_genuine_defects(self) -> None:
        findings = RuffAnalyzer().analyze("app/thing.py", PROBLEM_SOURCE)

        codes = {f.code for f in findings}
        assert "F401" in codes  # unused import
        assert "F821" in codes  # undefined name
        assert "E722" in codes  # bare except

    def test_reports_the_repository_path_not_the_temporary_one(self) -> None:
        """Findings are anchored to the file in the pull request, not the
        scratch file Ruff actually read."""
        findings = RuffAnalyzer().analyze("deep/nested/thing.py", PROBLEM_SOURCE)

        assert {f.file_path for f in findings} == {"deep/nested/thing.py"}

    def test_only_analyses_python(self) -> None:
        analyzer = RuffAnalyzer()

        assert analyzer.supports("a.py")
        assert analyzer.supports("a.pyi")
        assert not analyzer.supports("a.ts")
        assert not analyzer.supports("README.md")

    def test_selects_defect_rules_not_style_rules(self) -> None:
        """Style nits are the least useful thing an automated reviewer can say,
        and the project's own formatter already enforces them."""
        assert "E501" not in RUFF_RULES  # line too long
        assert "F" in RUFF_RULES
        assert "S" in RUFF_RULES

    def test_survives_a_file_that_does_not_parse(self) -> None:
        """A syntax error is a finding, not a crash."""
        findings = RuffAnalyzer().analyze("broken.py", "def (((:\n")

        assert isinstance(findings, list)


class TestMissingAnalyzer:
    def test_raises_rather_than_reporting_no_findings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Silence would look exactly like clean code."""
        monkeypatch.setattr(RuffAnalyzer, "is_available", staticmethod(lambda: False))

        with pytest.raises(AnalyzerUnavailableError):
            RuffAnalyzer().analyze("a.py", "x = 1\n")

    def test_the_failure_reaches_the_caller(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`analyze_file` swallows per-file analyser errors, but not this one:
        it is a deployment fault affecting every file, not a bad input."""
        monkeypatch.setattr(RuffAnalyzer, "is_available", staticmethod(lambda: False))

        with pytest.raises(AnalyzerUnavailableError):
            analyze_file(make_diff(), PROBLEM_SOURCE)


class TestChangedLineFiltering:
    def test_keeps_only_findings_on_changed_lines(self) -> None:
        # Line 1 holds the unused import; restrict the change to line 7.
        findings = analyze_file(make_diff(lines={7}), PROBLEM_SOURCE)

        assert all(f.line == 7 for f in findings)

    def test_can_be_disabled(self) -> None:
        unfiltered = analyze_file(make_diff(lines={7}), PROBLEM_SOURCE, changed_lines_only=False)

        assert any(f.line != 7 for f in unfiltered)

    def test_a_file_no_analyzer_supports_yields_nothing(self) -> None:
        assert analyze_file(make_diff(path="styles.css"), "body { color: red }") == []
