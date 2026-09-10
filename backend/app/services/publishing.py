"""Posting a completed review back to the pull request.

This is the only part of DevPilot that writes to someone else's repository, and
the only part whose mistakes are visible to a whole team. Three rules follow
from that.

**Post once.** Every finding records whether it has been posted and under which
comment id. A retried job, a redelivered webhook, or a worker that died after
posting but before committing must not produce a second copy of the same
comment.

**Post little.** Only findings at or above the confidence threshold, and only
those anchored to a line. A reviewer that posts its guesses is one people mute,
and a muted reviewer catches nothing.

**Never fail the review for a failed post.** The review is already computed and
stored. If GitHub rejects the comment, the finding stays on the dashboard and
the job still succeeds -- losing a stored review because a comment could not be
delivered would be trading the valuable thing for the cosmetic one.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.enums import FindingSeverity
from app.core.logging import get_logger
from app.db.models.finding import Finding
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.models.review import Review
from app.integrations.github.client import GitHubClient
from app.integrations.github.exceptions import GitHubError
from app.integrations.github.models import ReviewComment
from app.services.risk import describe_risk

logger = get_logger(__name__)

# GitHub renders a review body as Markdown. A leading marker makes DevPilot's
# reviews identifiable at a glance and greppable in a busy timeline.
REVIEW_HEADER = "### DevPilot review"

SEVERITY_LABEL: dict[FindingSeverity, str] = {
    FindingSeverity.CRITICAL: "🔴 Critical",
    FindingSeverity.HIGH: "🟠 High",
    FindingSeverity.MEDIUM: "🟡 Medium",
    FindingSeverity.LOW: "🔵 Low",
}


@dataclass(frozen=True)
class PublishResult:
    """What was posted, if anything."""

    posted: bool
    comment_count: int = 0
    github_review_id: int | None = None
    html_url: str | None = None
    skipped_reason: str | None = None


def publish_review(
    session: Session,
    *,
    review: Review,
    pull_request: PullRequest,
    repository: Repository,
    client: GitHubClient,
    settings: Settings | None = None,
) -> PublishResult:
    """Post a review's high-confidence findings to the pull request."""
    settings = settings or get_settings()

    if not settings.post_reviews_to_github:
        return PublishResult(posted=False, skipped_reason="posting is disabled")

    if repository.installation_id is None:
        return PublishResult(posted=False, skipped_reason="repository has no installation")

    findings = _unposted_postable_findings(
        session, review=review, minimum_confidence=settings.llm_min_confidence_to_post
    )

    if not findings:
        # Either everything was low-confidence, or this review has already been
        # posted. Both mean: say nothing rather than post an empty review.
        return PublishResult(posted=False, skipped_reason="no new findings worth posting")

    comments = [
        ReviewComment(
            path=finding.file_path,
            # `postable` guarantees a line; narrowed here for the type checker.
            line=finding.line or 1,
            body=render_comment(finding),
        )
        for finding in findings
    ]

    try:
        posted = client.create_pull_request_review(
            repository.installation_id,
            repository.full_name,
            pull_request.number,
            commit_sha=review.head_sha,
            body=render_summary(review, finding_count=len(comments)),
            comments=comments,
        )
    except GitHubError as exc:
        # The review is already stored and visible. Losing it because a comment
        # could not be delivered would trade the valuable thing for the
        # cosmetic one.
        logger.warning(
            "publishing.failed",
            review_id=str(review.id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return PublishResult(posted=False, skipped_reason=f"GitHub rejected the review: {exc}")

    # Marked only after GitHub confirms. A crash before this point re-posts on
    # retry, which is recoverable; marking first would silently lose comments.
    for finding in findings:
        finding.is_posted = True
        finding.github_comment_id = posted.review_id

    review.github_review_id = posted.review_id

    logger.info(
        "publishing.completed",
        review_id=str(review.id),
        github_review_id=posted.review_id,
        comments=len(comments),
    )
    return PublishResult(
        posted=True,
        comment_count=len(comments),
        github_review_id=posted.review_id,
        html_url=posted.html_url,
    )


def _unposted_postable_findings(
    session: Session, *, review: Review, minimum_confidence: float
) -> list[Finding]:
    """Findings worth posting that have not been posted already."""
    statement = (
        select(Finding)
        .where(
            Finding.review_id == review.id,
            Finding.is_posted.is_(False),
            Finding.confidence >= minimum_confidence,
            # A finding about the file as a whole has nowhere to anchor an
            # inline comment; it stays on the dashboard.
            Finding.line.isnot(None),
        )
        .order_by(Finding.file_path, Finding.line)
    )
    return list(session.execute(statement).scalars().all())


def render_summary(review: Review, *, finding_count: int) -> str:
    """The review body: what DevPilot found, and how sure it is overall.

    Deliberately states that the score is computed rather than judged, and that
    low-confidence findings were withheld. An automated reviewer that explains
    its own limits is easier to trust than one that presents itself as an
    oracle.
    """
    band = describe_risk(review.risk_score)

    lines = [
        REVIEW_HEADER,
        "",
        review.summary,
        "",
        f"**Risk score: {review.risk_score}/100 ({band})** — computed from finding "
        "severities, not chosen by the model.",
        "",
    ]

    if finding_count:
        lines.append(
            f"{finding_count} finding(s) are commented inline below. Anything "
            "DevPilot was less sure about is recorded in the dashboard rather "
            "than posted here."
        )
    else:
        lines.append("No findings confident enough to comment on.")

    lines.extend(["", "_Reviewed automatically. Treat it as a first pass, not a gate._"])
    return "\n".join(lines)


def render_comment(finding: Finding) -> str:
    """One inline comment.

    The severity, the category and the confidence are all stated, because a
    reader deciding whether to act on a comment needs to know how sure the tool
    was -- and a tool that hides its uncertainty gets trusted exactly once.
    """
    label = SEVERITY_LABEL.get(finding.severity, finding.severity.value)

    lines = [
        f"**{label} · {finding.category.value}** — {finding.title}",
        "",
        finding.description,
    ]

    if finding.suggestion:
        lines.extend(["", "**Suggestion**", "", finding.suggestion])

    lines.extend(["", f"<sub>DevPilot · confidence {finding.confidence:.0%}</sub>"])
    return "\n".join(lines)
