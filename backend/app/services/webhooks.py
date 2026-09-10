"""Webhook ingestion: idempotency and event handling.

Two properties define this module.

**Idempotency.** GitHub retries any delivery it believes failed, and a retry
must not produce a second review. The claim is made by *inserting* the delivery
id and letting the unique constraint reject a duplicate, rather than by checking
for one first. A check-then-insert has a window between the two statements in
which a concurrent retry also sees "not present" and both proceed; the database
has no such window.

**Nothing slow happens here.** The handlers write rows and return. Fetching
diffs, calling an LLM and posting comments all belong to the worker, because a
webhook that blocks on them times out, and GitHub reads a timeout as a failed
delivery and sends it again -- turning one slow review into several.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.enums import PullRequestState, ReviewJobStatus, WebhookEventStatus
from app.core.logging import get_logger
from app.db.models.pull_request import PullRequest
from app.db.models.review_job import ReviewJob
from app.db.models.user import User
from app.db.models.webhook_event import WebhookEvent
from app.db.repositories.repository import PullRequestStore, RepositoryStore
from app.db.repositories.review_job import ReviewJobStore
from app.db.repositories.user import UserRepository
from app.integrations.github.models import InstallationEvent, PullRequestEvent
from app.services import repositories as repository_service

logger = get_logger(__name__)

# Actions that mean the code under review has changed, so a fresh review is
# warranted. `edited` is absent on purpose: it fires for title and description
# edits, which do not change a single line of code.
REVIEWABLE_PULL_REQUEST_ACTIONS = frozenset(
    {"opened", "reopened", "synchronize", "ready_for_review"}
)

# Events DevPilot understands. Anything else is recorded and ignored rather
# than parsed, so an unmodelled event can never crash the endpoint.
HANDLED_EVENTS = frozenset({"ping", "installation", "installation_repositories", "pull_request"})


@dataclass(frozen=True)
class IngestionResult:
    """What the endpoint should report back to GitHub."""

    status: WebhookEventStatus
    detail: str
    # True when this exact delivery had already been recorded.
    duplicate: bool = False
    review_job_id: str | None = None

    @property
    def accepted_work(self) -> bool:
        return self.review_job_id is not None


class DuplicateDeliveryError(Exception):
    """This delivery id has already been recorded."""


def claim_delivery(
    session: Session,
    *,
    delivery_id: str,
    event_type: str,
    action: str | None,
    payload: dict[str, Any],
) -> WebhookEvent:
    """Record the delivery, or raise `DuplicateDeliveryError` if already seen.

    The insert happens inside a SAVEPOINT so that a constraint violation rolls
    back only this statement. Without it the whole transaction would be poisoned
    and the caller could not go on to respond.
    """
    event = WebhookEvent(
        delivery_id=delivery_id,
        event_type=event_type,
        action=action,
        payload=payload,
        status=WebhookEventStatus.RECEIVED,
    )
    try:
        with session.begin_nested():
            session.add(event)
            session.flush()
    except IntegrityError as exc:
        logger.info("webhook.duplicate_delivery", delivery_id=delivery_id)
        raise DuplicateDeliveryError(delivery_id) from exc
    return event


def process_event(
    session: Session,
    *,
    event: WebhookEvent,
) -> IngestionResult:
    """Dispatch a recorded delivery to its handler and record the outcome."""
    try:
        result = _dispatch(session, event=event)
    except ValidationError as exc:
        # The payload did not match what we expect for this event type. Retrying
        # cannot help -- GitHub would send the same bytes -- so this is recorded
        # as failed rather than left for redelivery.
        event.status = WebhookEventStatus.FAILED
        event.error_message = (
            f"Payload did not match the expected shape: {exc.error_count()} errors"
        )
        event.processed_at = datetime.now(UTC)
        logger.warning(
            "webhook.payload_invalid", delivery_id=event.delivery_id, event_type=event.event_type
        )
        return IngestionResult(
            status=WebhookEventStatus.FAILED, detail="Payload did not match the expected shape."
        )

    event.status = result.status
    event.processed_at = datetime.now(UTC)
    if result.status is WebhookEventStatus.IGNORED:
        event.error_message = None
    return result


def _dispatch(session: Session, *, event: WebhookEvent) -> IngestionResult:
    if event.event_type == "ping":
        # GitHub sends this once when a webhook is created, to prove the URL
        # works. There is nothing to do beyond answering successfully.
        return IngestionResult(status=WebhookEventStatus.PROCESSED, detail="pong")

    if event.event_type in {"installation", "installation_repositories"}:
        return _handle_installation(session, event=event)

    if event.event_type == "pull_request":
        return _handle_pull_request(session, event=event)

    return IngestionResult(
        status=WebhookEventStatus.IGNORED,
        detail=f"DevPilot does not act on `{event.event_type}` events.",
    )


# --- installation events -----------------------------------------------------


def _handle_installation(session: Session, *, event: WebhookEvent) -> IngestionResult:
    """Keep repository rows in step with what the App is installed on."""
    payload = InstallationEvent.model_validate(event.payload)
    installation_id = payload.installation.id

    owner = _resolve_owner(session, payload)
    if owner is None:
        # Nobody has linked the GitHub account that installed the App, so there
        # is no DevPilot user to own these repositories. Recorded, not an error:
        # the user may link afterwards and sync manually.
        logger.info(
            "webhook.installation_unclaimed",
            installation_id=installation_id,
            sender=payload.sender.login if payload.sender else None,
        )
        return IngestionResult(
            status=WebhookEventStatus.IGNORED,
            detail="No DevPilot account is linked to the GitHub user who installed the App.",
        )

    if payload.action in {"deleted", "suspend"}:
        deactivated = _deactivate_installation(session, installation_id=installation_id)
        return IngestionResult(
            status=WebhookEventStatus.PROCESSED,
            detail=f"Deactivated {deactivated} repositories.",
        )

    added = list(payload.repositories) + list(payload.repositories_added)
    for remote in added:
        repository_service.upsert_repository(
            session, owner=owner, remote=remote, installation_id=installation_id
        )

    removed = 0
    for remote in payload.repositories_removed:
        existing = RepositoryStore(session).get_by_github_id(remote.id)
        if existing is not None and existing.is_active:
            existing.is_active = False
            removed += 1

    logger.info(
        "webhook.installation_synced",
        installation_id=installation_id,
        action=payload.action,
        added=len(added),
        removed=removed,
    )
    return IngestionResult(
        status=WebhookEventStatus.PROCESSED,
        detail=f"Tracked {len(added)} repositories, removed {removed}.",
    )


def _resolve_owner(session: Session, payload: InstallationEvent) -> User | None:
    """Find the DevPilot account behind an installation.

    The sender is the person who clicked install, and their GitHub id was stored
    when they linked their account. Falling back to the installation's account
    covers a personal installation where sender and account are the same person.
    """
    users = UserRepository(session)
    candidates = [payload.sender, payload.installation.account]
    for candidate in candidates:
        if candidate is None:
            continue
        user = users.get_by_github_id(candidate.id)
        if user is not None:
            return user
    return None


def _deactivate_installation(session: Session, *, installation_id: int) -> int:
    """Mark every repository under an installation inactive, keeping its history."""
    deactivated = 0
    for repository in RepositoryStore(session).list_for_installation(installation_id):
        if repository.is_active:
            repository.is_active = False
            deactivated += 1
    return deactivated


# --- pull request events -----------------------------------------------------


def _handle_pull_request(session: Session, *, event: WebhookEvent) -> IngestionResult:
    payload = PullRequestEvent.model_validate(event.payload)

    repository = RepositoryStore(session).get_by_github_id(payload.repository.id)
    if repository is None:
        # A delivery for a repository DevPilot does not track. Recorded so the
        # retry is recognised, but there is nothing to review.
        logger.info("webhook.unknown_repository", full_name=payload.repository.full_name)
        return IngestionResult(
            status=WebhookEventStatus.IGNORED,
            detail=f"Repository {payload.repository.full_name} is not tracked by DevPilot.",
        )

    event.repository_id = repository.id
    pull_request = _upsert_pull_request(session, repository_id=repository.id, payload=payload)

    if payload.action not in REVIEWABLE_PULL_REQUEST_ACTIONS:
        return IngestionResult(
            status=WebhookEventStatus.PROCESSED,
            detail=f"Recorded `{payload.action}`; no review needed.",
        )

    if payload.pull_request.draft:
        # A draft is explicitly not ready for review. Reviewing it would spend
        # an LLM call on code the author is still writing.
        return IngestionResult(
            status=WebhookEventStatus.PROCESSED,
            detail="Pull request is a draft; no review queued.",
        )

    if not repository.is_active:
        return IngestionResult(
            status=WebhookEventStatus.PROCESSED,
            detail="Repository is not active; no review queued.",
        )

    job = _queue_review(session, pull_request=pull_request, event=event)
    if job is None:
        return IngestionResult(
            status=WebhookEventStatus.PROCESSED,
            detail="A review for this commit is already queued.",
        )

    return IngestionResult(
        status=WebhookEventStatus.PROCESSED,
        detail="Review queued.",
        review_job_id=str(job.id),
    )


def _upsert_pull_request(
    session: Session, *, repository_id: Any, payload: PullRequestEvent
) -> PullRequest:
    """Create or refresh the pull request row from the delivery."""
    store = PullRequestStore(session)
    existing = store.get_by_number(repository_id, payload.number)

    state = _resolve_state(payload)

    if existing is None:
        return store.add(
            PullRequest(
                repository_id=repository_id,
                github_pr_id=payload.pull_request.id,
                number=payload.number,
                title=payload.pull_request.title,
                author_login=payload.pull_request.user.login if payload.pull_request.user else "",
                state=state,
                head_sha=payload.pull_request.head.sha,
                base_sha=payload.pull_request.base.sha,
                head_ref=payload.pull_request.head.ref,
                base_ref=payload.pull_request.base.ref,
            )
        )

    existing.title = payload.pull_request.title
    existing.state = state
    existing.head_sha = payload.pull_request.head.sha
    existing.base_sha = payload.pull_request.base.sha
    existing.head_ref = payload.pull_request.head.ref
    existing.base_ref = payload.pull_request.base.ref
    return existing


def _resolve_state(payload: PullRequestEvent) -> PullRequestState:
    """Map GitHub's state to ours.

    GitHub reports a merged pull request as `closed` with `merged: true`, so the
    two must be combined -- otherwise a merge is indistinguishable from an
    abandonment.
    """
    if payload.pull_request.merged:
        return PullRequestState.MERGED
    if payload.pull_request.state == "closed":
        return PullRequestState.CLOSED
    return PullRequestState.OPEN


def _queue_review(
    session: Session, *, pull_request: PullRequest, event: WebhookEvent
) -> ReviewJob | None:
    """Create a queued review job for the pull request's current commit.

    Returns `None` when an unfinished job already covers this exact commit.

    The job is only *recorded* here. Handing it to a worker is Milestone 5; until
    then rows accumulate in `queued` and nothing processes them.
    """
    jobs = ReviewJobStore(session)
    head_sha = pull_request.head_sha

    if jobs.find_active_for_commit(pull_request.id, head_sha) is not None:
        logger.info(
            "webhook.review_already_queued",
            pull_request_id=str(pull_request.id),
            head_sha=head_sha,
        )
        return None

    cancelled = jobs.cancel_superseded(pull_request.id, keep_head_sha=head_sha)
    if cancelled:
        logger.info(
            "webhook.superseded_jobs_cancelled",
            pull_request_id=str(pull_request.id),
            cancelled=cancelled,
        )

    job = jobs.add(
        ReviewJob(
            pull_request_id=pull_request.id,
            webhook_event_id=event.id,
            head_sha=head_sha,
            status=ReviewJobStatus.QUEUED,
        )
    )
    logger.info(
        "webhook.review_queued",
        review_job_id=str(job.id),
        pull_request_id=str(pull_request.id),
        head_sha=head_sha,
    )
    return job
