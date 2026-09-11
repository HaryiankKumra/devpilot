"""The lifecycle of a review job: claim it, finish it, or record why it failed.

Kept separate from the Celery task so the state machine can be tested by calling
functions, with no broker and no worker. The task in `app.worker.tasks` is a thin
wrapper that supplies a session and decides whether to retry.

The states are:

    queued ──claim──▶ running ──▶ succeeded
                         │
                         ├──▶ failed     (permanent, or attempts exhausted)
                         └──▶ queued     (transient failure, will be retried)

    queued/running ──▶ cancelled          (superseded by a newer commit)
"""

from __future__ import annotations

import traceback
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, update
from sqlalchemy.orm import Session

from app.core.enums import ReviewJobStatus
from app.core.logging import get_logger
from app.db.models.review_job import ReviewJob
from app.db.models.user import User

logger = get_logger(__name__)

# Error text is stored for humans to read, not to be re-parsed. A wedged loop
# can produce megabytes of traceback, and an unbounded TEXT column full of it
# helps nobody.
MAX_ERROR_MESSAGE_CHARS = 2_000
MAX_TRACEBACK_CHARS = 20_000


class JobNotClaimableError(Exception):
    """The job is not in a state a worker may start.

    Raised when the row has already been claimed, cancelled, or does not exist.
    This is an expected outcome, not a fault: it is what happens when a task is
    delivered twice.
    """


def claim(session: Session, job_id: uuid.UUID) -> ReviewJob:
    """Move a queued job to `running` and return it.

    The transition is a single conditional UPDATE rather than a read followed by
    a write. Celery redelivers a task whenever a worker dies mid-job, and with
    `acks_late` enabled that happens routinely -- so two workers can hold the
    same task at once. Checking the status in Python and then writing it leaves
    a window where both see `queued` and both proceed. Letting the database
    match on `status = 'queued'` closes it: exactly one UPDATE reports a row.
    """
    # Cast because `Session.execute` is typed as returning `Result`, while
    # `rowcount` -- the thing that tells us whether we won the race -- is
    # only declared on `CursorResult`, which DML actually returns.
    result = cast(
        CursorResult[Any],
        session.execute(
            update(ReviewJob)
            .where(ReviewJob.id == job_id, ReviewJob.status == ReviewJobStatus.QUEUED)
            .values(
                status=ReviewJobStatus.RUNNING,
                started_at=datetime.now(UTC),
                attempts=ReviewJob.attempts + 1,
            )
        ),
    )

    if result.rowcount == 0:
        # Either another worker won the race, the job was cancelled by a newer
        # push, or the id is unknown. All three mean: do not run it.
        raise JobNotClaimableError(str(job_id))

    job = session.get(ReviewJob, job_id)
    if job is None:  # pragma: no cover - the UPDATE above proved it exists
        raise JobNotClaimableError(str(job_id))

    session.refresh(job)
    logger.info("review_job.claimed", review_job_id=str(job.id), attempt=job.attempts)
    return job


def mark_succeeded(session: Session, job: ReviewJob) -> ReviewJob:
    """Record that the job produced a review."""
    job.status = ReviewJobStatus.SUCCEEDED
    job.finished_at = datetime.now(UTC)
    job.error_type = None
    job.error_message = None
    job.error_traceback = None
    logger.info(
        "review_job.succeeded",
        review_job_id=str(job.id),
        attempts=job.attempts,
        duration_seconds=_duration_seconds(job),
    )
    return job


def mark_failed(session: Session, job: ReviewJob, error: BaseException) -> ReviewJob:
    """Record a terminal failure, with enough detail to debug it later.

    The three error columns serve different readers: `error_type` is
    low-cardinality and groupable ("how often does the LLM time out?"),
    `error_message` is what a user sees, and `error_traceback` is for whoever
    has to fix it.
    """
    job.status = ReviewJobStatus.FAILED
    job.finished_at = datetime.now(UTC)
    job.error_type = type(error).__name__
    job.error_message = str(error)[:MAX_ERROR_MESSAGE_CHARS]
    job.error_traceback = _format_traceback(error)[:MAX_TRACEBACK_CHARS]
    logger.warning(
        "review_job.failed",
        review_job_id=str(job.id),
        attempts=job.attempts,
        error_type=job.error_type,
    )
    return job


def mark_for_retry(session: Session, job: ReviewJob, error: BaseException) -> ReviewJob:
    """Return the job to the queue after a transient failure.

    The error detail is kept even though the job will run again: if it later
    exhausts its attempts, the reason for each earlier failure is the only
    record of what went wrong.
    """
    job.status = ReviewJobStatus.QUEUED
    job.started_at = None
    job.error_type = type(error).__name__
    job.error_message = str(error)[:MAX_ERROR_MESSAGE_CHARS]
    job.error_traceback = _format_traceback(error)[:MAX_TRACEBACK_CHARS]
    logger.info(
        "review_job.retrying",
        review_job_id=str(job.id),
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        error_type=job.error_type,
    )
    return job


def _as_utc(moment: datetime) -> datetime:
    """Treat a naive timestamp as UTC.

    The columns are declared `timezone=True`, and PostgreSQL honours that. Some
    other backends -- SQLite, which the fast tests use -- hand back naive
    datetimes anyway, and subtracting one of those from an aware one raises.
    Normalising here keeps a logging helper from being able to crash a job that
    otherwise succeeded.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _duration_seconds(job: ReviewJob) -> float | None:
    if job.started_at is None or job.finished_at is None:
        return None
    elapsed = _as_utc(job.finished_at) - _as_utc(job.started_at)
    return round(elapsed.total_seconds(), 3)


def _format_traceback(error: BaseException) -> str:
    return "".join(traceback.format_exception(type(error), error, error.__traceback__))


def retry_failed_review(session: Session, *, owner: User, pull_request_id: uuid.UUID) -> ReviewJob:
    """Queue a fresh attempt for a pull request whose last attempt failed.

    Exists because a review can fail for reasons that have nothing to do with
    the code -- the model provider returning 503 three times in two minutes,
    say -- and the only other way to get another attempt is to push a commit.
    Asking the author to change their pull request to work around our provider
    is the wrong way round.

    A *new* job rather than a reset of the failed one: the failed row is the
    record of what went wrong and when, and overwriting it would erase exactly
    the history the pull request page exists to show.

    Only the latest attempt matters, and only if it failed:

    * queued or running -- an attempt is already in flight; a second would race
      it for the same commit.
    * succeeded -- there is a review already; a second would post twice.
    * cancelled -- superseded by a newer commit; that commit has its own job.
    """
    from sqlalchemy import select

    from app.core.exceptions import ConflictError, NotFoundError
    from app.db.models.pull_request import PullRequest
    from app.db.models.repository import Repository
    from app.db.repositories.review_job import ReviewJobStore

    owned = (
        select(PullRequest)
        .join(Repository, PullRequest.repository_id == Repository.id)
        .where(PullRequest.id == pull_request_id, Repository.owner_id == owner.id)
    )
    pull_request = session.execute(owned).scalar_one_or_none()
    if pull_request is None:
        # 404 for someone else's pull request: confirming it exists is itself
        # information the caller has not earned.
        raise NotFoundError("Pull request not found.")

    store = ReviewJobStore(session)
    attempts = store.list_for_pull_request(pull_request.id)
    # Normalised, because SQLite hands back the server-default timestamp naive
    # and an explicitly set one aware, and Python refuses to compare the two.
    latest = max(attempts, key=lambda job: _as_utc(job.created_at), default=None)

    if latest is None:
        raise ConflictError("This pull request has never been reviewed; nothing to retry.")
    if latest.status in (ReviewJobStatus.QUEUED, ReviewJobStatus.RUNNING):
        raise ConflictError("A review is already in progress for this pull request.")
    if latest.status is ReviewJobStatus.SUCCEEDED:
        raise ConflictError("The latest review succeeded; there is nothing to retry.")
    if latest.status is ReviewJobStatus.CANCELLED:
        raise ConflictError(
            "That attempt was superseded by a newer commit, which has its own review."
        )

    job = store.add(
        ReviewJob(
            pull_request_id=pull_request.id,
            # No webhook triggered this; a person did.
            webhook_event_id=None,
            head_sha=latest.head_sha,
            status=ReviewJobStatus.QUEUED,
        )
    )
    logger.info(
        "review_job.retry_requested",
        review_job_id=str(job.id),
        pull_request_id=str(pull_request.id),
        previous_job_id=str(latest.id),
        previous_error=latest.error_type,
    )
    return job
