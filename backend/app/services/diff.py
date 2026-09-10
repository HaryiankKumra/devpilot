"""Parsing unified diffs.

The parser exists to answer one question precisely: **which lines did this pull
request actually change?**

That matters more than it sounds. A linter run over a changed file reports
everything wrong with the file, including problems that predate the pull
request. Posting those as review comments is how an automated reviewer becomes
noise that people mute -- the author did not write that code and cannot be
expected to fix it here. Knowing the changed line numbers lets findings be
filtered down to what the author is responsible for.

Parsing is done by hand rather than with a library because the subset of the
format DevPilot needs is small and completely specified, and a hand-rolled
parser can be read and tested in one sitting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.logging import get_logger

logger = get_logger(__name__)

# `@@ -oldStart,oldCount +newStart,newCount @@ optional context`
# The counts are omitted when they are 1, which is why they are optional here.
HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)

DIFF_HEADER = re.compile(r"^diff --git a/(?P<old>.+?) b/(?P<new>.+)$")


class ChangeType(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass
class FileDiff:
    """The changes to one file."""

    path: str
    change_type: ChangeType
    # Set for renames; `path` is always the new location.
    previous_path: str | None = None

    # Line numbers **in the new file** that this pull request introduced or
    # altered. This is the set that findings are filtered against.
    added_lines: set[int] = field(default_factory=set)
    # Line numbers in the old file that were removed. Kept because a deletion
    # can itself be the bug -- a dropped validation call, say.
    removed_line_count: int = 0

    # True when GitHub reported the file as binary. There is nothing to analyse
    # and nothing to show, but the change should still be listed.
    is_binary: bool = False

    # The raw hunk text for this file, exactly as GitHub sent it. Kept because
    # the LLM has to actually see the code: line numbers alone tell it where a
    # change is but not what the change says.
    patch_lines: list[str] = field(default_factory=list)

    @property
    def patch(self) -> str:
        return "\n".join(self.patch_lines)

    @property
    def is_reviewable(self) -> bool:
        """Whether there is any new source for a reviewer to look at."""
        return not self.is_binary and self.change_type is not ChangeType.DELETED

    @property
    def added_line_count(self) -> int:
        return len(self.added_lines)


@dataclass
class ParsedDiff:
    """A whole pull request diff."""

    files: list[FileDiff]
    # True when the diff was cut short by a size limit, so callers can say so
    # rather than silently reviewing part of a change.
    truncated: bool = False

    @property
    def total_added_lines(self) -> int:
        return sum(f.added_line_count for f in self.files)

    @property
    def reviewable_files(self) -> list[FileDiff]:
        return [f for f in self.files if f.is_reviewable]

    def find(self, path: str) -> FileDiff | None:
        return next((f for f in self.files if f.path == path), None)


def parse_unified_diff(diff_text: str) -> ParsedDiff:
    """Parse a unified diff into per-file change sets."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    new_line_number = 0
    in_hunk = False

    for line in diff_text.splitlines():
        header = DIFF_HEADER.match(line)
        if header:
            if current is not None:
                files.append(current)
            # Assume a modification until a marker says otherwise; `new file
            # mode` and `deleted file mode` follow this line when they apply.
            current = FileDiff(path=header.group("new"), change_type=ChangeType.MODIFIED)
            in_hunk = False
            continue

        if current is None:
            # Text before the first `diff --git`, which GitHub does not send.
            continue

        if line.startswith("new file mode"):
            current.change_type = ChangeType.ADDED
            continue
        if line.startswith("deleted file mode"):
            current.change_type = ChangeType.DELETED
            continue
        if line.startswith("rename from "):
            current.change_type = ChangeType.RENAMED
            current.previous_path = line.removeprefix("rename from ").strip()
            continue
        if line.startswith("rename to "):
            current.path = line.removeprefix("rename to ").strip()
            continue
        if line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            current.is_binary = True
            continue

        hunk = HUNK_HEADER.match(line)
        if hunk:
            new_line_number = int(hunk.group("new_start"))
            in_hunk = True
            current.patch_lines.append(line)
            continue

        if not in_hunk:
            # `index`, `---`, `+++` and similar metadata.
            continue

        if line.startswith("+"):
            # `+++` is metadata, not an added line, and only appears outside a
            # hunk -- but guard anyway rather than recording a phantom line.
            if not line.startswith("+++"):
                current.added_lines.add(new_line_number)
                current.patch_lines.append(line)
                new_line_number += 1
            continue

        if line.startswith("-"):
            if not line.startswith("---"):
                current.removed_line_count += 1
                current.patch_lines.append(line)
            continue

        if line.startswith("\\"):
            # "\ No newline at end of file" belongs to the previous line.
            continue

        # A context line: unchanged, but it advances position in the new file.
        current.patch_lines.append(line)
        new_line_number += 1

    if current is not None:
        files.append(current)

    return ParsedDiff(files=files)


def changed_line_ranges(file_diff: FileDiff, *, context: int = 0) -> list[tuple[int, int]]:
    """Collapse changed line numbers into contiguous ranges.

    Useful for showing a finding in context, and for asking an LLM about a
    region rather than a scatter of individual line numbers. `context` widens
    each range by that many lines on both sides.
    """
    if not file_diff.added_lines:
        return []

    ordered = sorted(file_diff.added_lines)
    ranges: list[tuple[int, int]] = []
    start = previous = ordered[0]

    for line in ordered[1:]:
        # A gap wider than the context means a genuinely separate region.
        if line - previous > max(1, context * 2):
            ranges.append((max(1, start - context), previous + context))
            start = line
        previous = line

    ranges.append((max(1, start - context), previous + context))
    return ranges
