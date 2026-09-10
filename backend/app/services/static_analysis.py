"""Static analysis of changed files.

Two decisions shape this module.

**Only analysers that parse, never execute.** The input is code from a stranger's
pull request. A tool that imports the module under test, resolves plugins from
the repository, or honours a config file checked into it, is a remote code
execution vector pointed at our worker. Ruff parses source into an AST and never
runs it, and is invoked with `--isolated` so a `pyproject.toml` in the pull
request cannot influence how it behaves.

**Report only on lines the pull request changed.** Running a linter over a
changed file surfaces everything wrong with that file, most of which predates
the change. Posting those as review comments is how an automated reviewer
becomes noise people mute. The diff tells us which lines the author is
responsible for, and findings outside that set are discarded.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.core.logging import get_logger
from app.services.diff import FileDiff

logger = get_logger(__name__)

# Ruff on one file is fast. A limit this generous only trips on pathological
# input, which is exactly when we want to give up rather than wedge a worker.
ANALYSIS_TIMEOUT_SECONDS = 30

# Rules worth raising in review. Deliberately not the full default set: style
# preferences ("line too long") are the reviewer's least useful contribution and
# are usually enforced by the project's own formatter anyway. These are the
# categories that indicate a defect.
RUFF_RULES = (
    "E9",  # syntax and runtime errors
    "E722",  # bare `except:`, which silently swallows every failure
    "F",  # pyflakes: undefined names, unused imports, unreachable code
    "B",  # bugbear: mutable defaults, loop-variable binding, silent surprises
    "S",  # bandit: hardcoded secrets, subprocess misuse, weak crypto
)


@dataclass(frozen=True)
class StaticFinding:
    """One issue reported by an analyser."""

    analyzer: str
    # Rule identifier, e.g. `F401`. Kept so a finding can be looked up and,
    # later, so repeated noise can be suppressed by rule.
    code: str
    message: str
    file_path: str
    line: int
    column: int | None = None
    url: str | None = None

    def describe(self) -> str:
        """One-line rendering, used when building the LLM prompt."""
        return f"{self.file_path}:{self.line} {self.code} {self.message}"


class Analyzer(Protocol):
    """A static analysis tool DevPilot can run over a single file."""

    name: str

    def supports(self, path: str) -> bool: ...

    def analyze(self, path: str, content: str) -> list[StaticFinding]: ...


class AnalyzerUnavailableError(Exception):
    """An analyser is configured but not installed.

    Raised rather than returning no findings. Silence here is indistinguishable
    from clean code, so a missing analyser would quietly downgrade every review
    while still reporting success -- the worst possible failure for a tool whose
    output people are meant to trust.
    """


class RuffAnalyzer:
    """Runs Ruff over Python files.

    Ruff is already a development dependency of this project, so it needs no
    extra install, and it is a parser rather than an interpreter -- it cannot be
    made to execute the code it inspects.
    """

    name = "ruff"

    def supports(self, path: str) -> bool:
        return path.endswith((".py", ".pyi"))

    @staticmethod
    @lru_cache(maxsize=1)
    def is_available() -> bool:
        """Whether Ruff can actually be invoked in this process.

        Cached: the answer cannot change while the process lives, and this runs
        before every file otherwise.
        """
        try:
            # Fixed argument vector, no shell: nothing from the analysed pull
            # request reaches the command line.
            completed = subprocess.run(
                [sys.executable, "-m", "ruff", "--version"],
                capture_output=True,
                text=True,
                timeout=ANALYSIS_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    def analyze(self, path: str, content: str) -> list[StaticFinding]:
        """Analyse one file's contents, returning findings with real line numbers.

        Raises `AnalyzerUnavailableError` if Ruff is not installed, so a missing
        analyser fails the review rather than silently producing none.
        """
        if not self.is_available():
            raise AnalyzerUnavailableError(
                "Ruff is not installed in this environment, so Python files cannot be analysed."
            )

        # Written to a temporary file rather than piped on stdin so that Ruff
        # reports the original filename in its output and applies any
        # extension-specific behaviour.
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / Path(path).name
            try:
                target.write_text(content, encoding="utf-8")
            except (OSError, UnicodeEncodeError) as exc:
                logger.info("static_analysis.unwritable", path=path, error=str(exc))
                return []

            raw = self._run_ruff(target)

        return [finding for item in raw if (finding := self._to_finding(item, path))]

    def _run_ruff(self, target: Path) -> list[dict[str, Any]]:
        command = [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--output-format=json",
            # Ignore any configuration in the analysed tree. Without this, a
            # pull request could disable the rules that would flag it -- or
            # point Ruff at settings that change its behaviour entirely.
            "--isolated",
            # Ruff writes a cache into the working directory by default, which
            # fails in the container: the image runs as an unprivileged user and
            # /app is root-owned. Caching buys nothing here anyway -- each call
            # analyses one temporary file that is deleted immediately after.
            "--no-cache",
            f"--select={','.join(RUFF_RULES)}",
            str(target),
        ]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=ANALYSIS_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("static_analysis.timed_out", analyzer=self.name, file=target.name)
            return []
        except OSError as exc:
            logger.warning("static_analysis.unavailable", analyzer=self.name, error=str(exc))
            return []

        # Ruff exits 1 when it finds problems, which is success for us. Higher
        # codes mean it could not run at all.
        if completed.returncode not in (0, 1):
            logger.warning(
                "static_analysis.failed",
                analyzer=self.name,
                returncode=completed.returncode,
                stderr=completed.stderr[:500],
            )
            return []

        try:
            parsed = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError:
            logger.warning("static_analysis.unparseable_output", analyzer=self.name)
            return []

        return parsed if isinstance(parsed, list) else []

    def _to_finding(self, item: dict[str, Any], path: str) -> StaticFinding | None:
        location = item.get("location") or {}
        line = location.get("row")
        if not isinstance(line, int) or line < 1:
            return None

        return StaticFinding(
            analyzer=self.name,
            code=str(item.get("code") or "unknown"),
            message=str(item.get("message") or "").strip(),
            # Report the repository path, not the temporary one Ruff saw.
            file_path=path,
            line=line,
            column=location.get("column"),
            url=item.get("url"),
        )


DEFAULT_ANALYZERS: tuple[Analyzer, ...] = (RuffAnalyzer(),)


@dataclass
class AnalysisResult:
    """Findings, plus whether anything failed to run.

    The failure count exists so a degraded review can say so. Returning an empty
    list when an analyser crashed is indistinguishable from finding nothing
    wrong, which quietly overstates how carefully the code was checked.
    """

    findings: list[StaticFinding] = dataclass_field(default_factory=list)
    failed_analyzers: list[str] = dataclass_field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.failed_analyzers


def analyze_file_detailed(
    file_diff: FileDiff,
    content: str,
    *,
    analyzers: tuple[Analyzer, ...] = DEFAULT_ANALYZERS,
    changed_lines_only: bool = True,
) -> AnalysisResult:
    """Run every applicable analyser over one file.

    When `changed_lines_only` is set, findings on lines this pull request did
    not touch are discarded -- the author did not write that code and cannot be
    asked to fix it here.
    """
    findings: list[StaticFinding] = []
    failed: list[str] = []

    for analyzer in analyzers:
        if not analyzer.supports(file_diff.path):
            continue
        try:
            findings.extend(analyzer.analyze(file_diff.path, content))
        except AnalyzerUnavailableError:
            # A deployment problem, not a problem with this file. Let it out so
            # the job records why the review is incomplete.
            raise
        except Exception as exc:
            # One broken analyser must not fail the whole review. The other
            # stages, and the LLM, still have something useful to say.
            logger.warning(
                "static_analysis.analyzer_error",
                analyzer=analyzer.name,
                path=file_diff.path,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            failed.append(analyzer.name)

    if changed_lines_only:
        findings = [f for f in findings if f.line in file_diff.added_lines]

    return AnalysisResult(findings=findings, failed_analyzers=failed)


def analyze_file(
    file_diff: FileDiff,
    content: str,
    *,
    analyzers: tuple[Analyzer, ...] = DEFAULT_ANALYZERS,
    changed_lines_only: bool = True,
) -> list[StaticFinding]:
    """Findings only, for callers that do not need the completeness signal."""
    return analyze_file_detailed(
        file_diff,
        content,
        analyzers=analyzers,
        changed_lines_only=changed_lines_only,
    ).findings
