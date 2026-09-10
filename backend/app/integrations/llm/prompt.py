"""Building the review prompt.

Kept as pure functions over a `ReviewContext` so the exact text sent to the
model can be asserted in tests and printed during debugging. A prompt assembled
inline inside a provider call is a prompt nobody ever reads.

The system prompt is deliberately stable -- it contains nothing derived from the
pull request. That is what makes it cacheable, and it is where the review's
standards live. The user prompt carries everything that varies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.diff import FileDiff
from app.services.retrieval import RetrievedChunk
from app.services.static_analysis import StaticFinding

if TYPE_CHECKING:
    # Imported for typing only: `review_pipeline` imports this module at
    # runtime, and a runtime import back would be a cycle.
    from app.services.review_pipeline import ReviewContext

# How much of a single file's patch to include. A pathological file should not
# be able to crowd out every other file in the change.
MAX_DIFF_CHARS_PER_FILE = 20_000
# Total budget across all files, so a wide change degrades gracefully instead of
# producing a prompt that overruns the context window.
MAX_TOTAL_DIFF_CHARS = 120_000

# Beyond this many, listing individual line numbers stops being useful and
# starts consuming the budget the patch itself needs.
MAX_LISTED_LINE_NUMBERS = 200

SYSTEM_PROMPT = """\
You are a senior engineer reviewing a pull request. You are thorough, direct, \
and you do not pad your output.

What to report:
- Bugs: logic that is wrong, unhandled failure cases, race conditions, \
off-by-one errors, incorrect error handling.
- Security: injection, missing authorisation checks, secrets in code, unsafe \
deserialisation, weak crypto.
- Performance: N+1 queries, unbounded loops over remote calls, work repeated \
inside a loop that could be hoisted out.
- Quality: only where it genuinely risks a defect later. Not formatting, not \
naming preferences, not missing docstrings.

Rules you must follow:
- Only comment on lines this pull request changed. Each file lists the lines \
you may comment on; anything outside them is not the author's responsibility \
in this change.
- Every `file` value must be copied exactly from the diff you are shown. Never \
invent a path.
- Every `line` value must be one of the lines listed for that file. If a \
problem concerns the file as a whole, set `line` to null rather than guessing.
- Set `confidence` honestly. Below 0.5 means you are speculating. It is better \
to report a real problem at 0.6 than to inflate it to 0.9.
- Report nothing rather than something trivial. An empty `findings` list is a \
good answer for a clean pull request, and far more useful than invented \
nitpicks.
- Do not restate what the static analyser already found. It is shown to you so \
you can build on it, not repeat it.

The summary is written for the pull request's author: two or three sentences on \
what the change does and whether it looks safe to merge.\
"""


def build_system_prompt() -> str:
    """The stable half of the prompt.

    Contains nothing from the pull request, so it is byte-identical on every
    request and can be cached by the provider.
    """
    return SYSTEM_PROMPT


def build_user_prompt(
    context: ReviewContext,
    *,
    repository_full_name: str,
    pull_request_title: str,
    pull_request_number: int,
) -> str:
    """Assemble everything that varies for this particular review."""
    sections: list[str] = [
        f"Repository: {repository_full_name}",
        f"Pull request #{pull_request_number}: {pull_request_title}",
        "",
        _describe_change(context),
        "",
        _render_static_findings(context.static_findings),
        "",
        _render_retrieved_context(context.retrieved_chunks),
        "",
        "## Diff",
        "",
        _render_diff(context),
    ]

    if context.skipped_files:
        sections.extend(
            [
                "",
                "## Not analysed",
                "",
                "These files changed but could not be read (too large, or removed "
                "later in the branch). Do not comment on them:",
                *(f"- {path}" for path in context.skipped_files),
            ]
        )

    return "\n".join(sections)


def _describe_change(context: ReviewContext) -> str:
    return (
        "## Change summary\n\n"
        f"{len(context.diff.files)} file(s) changed, "
        f"{context.diff.total_added_lines} line(s) added or modified."
    )


def _render_static_findings(findings: list[StaticFinding]) -> str:
    """Show what the analyser already found, so the model does not repeat it."""
    if not findings:
        return (
            "## Static analysis\n\n"
            "The static analyser reported nothing on the changed lines. That does "
            "not mean the change is correct -- it only sees what a parser can see."
        )

    lines = [
        "## Static analysis",
        "",
        "Already reported by the analyser. Do not repeat these; look for what a parser cannot see:",
        "",
    ]
    lines.extend(f"- {finding.describe()}" for finding in findings)
    return "\n".join(lines)


def _render_retrieved_context(chunks: list[RetrievedChunk]) -> str:
    """Show related repository code the diff refers to but does not contain.

    Each chunk is labelled with its real file and line range, and the section
    says plainly that this code is *not* under review -- otherwise the model
    reports problems in it, and those findings are then discarded by validation
    for citing lines outside the diff.
    """
    if not chunks:
        return (
            "## Related repository code\n\n"
            "None retrieved. Either the repository has not been indexed yet, or "
            "nothing in it closely matches this change. Review the diff on its own."
        )

    sections = [
        "## Related repository code",
        "",
        "Existing code from elsewhere in this repository, retrieved because it "
        "resembles what the change touches. **This code is not under review** -- "
        "it is context. Do not report findings against it; use it to judge "
        "whether the change is correct and consistent with what is already there.",
        "",
    ]

    for chunk in chunks:
        sections.append(f"#### {chunk.describe_location()}")
        sections.append("")
        sections.append(f"```\n{chunk.content}\n```")
        sections.append("")

    return "\n".join(sections).rstrip()


def _render_diff(context: ReviewContext) -> str:
    """Render the diff, truncating rather than overrunning the context window."""
    rendered: list[str] = []
    budget = MAX_TOTAL_DIFF_CHARS

    for file_diff in context.diff.files:
        if budget <= 0:
            rendered.append("[Remaining files omitted: the diff exceeded the prompt budget.]")
            break

        block = _render_file(file_diff, budget=min(MAX_DIFF_CHARS_PER_FILE, budget))
        rendered.append(block)
        budget -= len(block)

    return "\n\n".join(rendered)


def _render_file(file_diff: FileDiff, *, budget: int) -> str:
    """One file's entry: its path, the lines in scope, and the actual patch."""
    header = f"### {file_diff.path} ({file_diff.change_type.value})"

    if file_diff.is_binary:
        return f"{header}\n\n[Binary file; not shown.]"
    if not file_diff.is_reviewable:
        return f"{header}\n\n[File deleted; nothing to review.]"

    changed = sorted(file_diff.added_lines)
    if not changed:
        return f"{header}\n\n[No added lines.]"

    # The permitted line numbers are stated explicitly as well as being visible
    # in the patch. Deriving a line number by counting through hunk headers is
    # arithmetic models are unreliable at, and every wrong answer becomes a
    # comment on the wrong line of somebody's pull request.
    listed = ", ".join(str(line) for line in changed[:MAX_LISTED_LINE_NUMBERS])
    if len(changed) > MAX_LISTED_LINE_NUMBERS:
        listed += ", ..."

    patch = file_diff.patch
    if len(patch) > budget:
        patch = patch[:budget] + "\n[... truncated ...]"

    return f"{header}\n\nLines you may comment on: {listed}\n\n```diff\n{patch}\n```"
