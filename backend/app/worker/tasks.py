"""Celery tasks.

The task itself is deliberately thin. It owns three things a plain function
cannot: a database session, the decision to retry, and the translation of an
exception into a job state. Everything else lives in the service layer, where it
can be tested by calling a function.

The task takes a **job id**, not a job. Celery arguments travel through Redis as
JSON, so passing a row would mean serialising state that may be stale by the
time a worker picks it up -- and after a retry, hours stale. The database is the
source of truth; the message is only a pointer to it.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.session import session_scope
from app.integrations.embeddings.provider import (
    EmbeddingRateLimitError,
    EmbeddingTransientError,
)
from app.integrations.github.exceptions import GitHubRateLimitError, GitHubTransientError
from app.integrations.llm.provider import LLMRateLimitError, LLMTransientError
from app.services import review_jobs, review_pipeline
from app.services.review_jobs import JobNotClaimableError
from app.worker.celery_app import celery_app

logger = get_logger(__name__)

REVIEW_TASK_NAME = "devpilot.review_pull_request"
INDEX_TASK_NAME = "devpilot.index_repository"

# Failures worth another attempt: the network faltered, or GitHub asked us to
# slow down. Everything else is treated as permanent, because retrying a bug
# three times just produces the same bug three times more slowly.
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    GitHubTransientError,
    GitHubRateLimitError,
    # `LLMRateLimitError` subclasses `LLMTransientError`, but both are listed so
    # the intent survives a future refactor of that hierarchy.
    LLMTransientError,
    LLMRateLimitError,
    EmbeddingTransientError,
    EmbeddingRateLimitError,
    ConnectionError,
    TimeoutError,
)


def _retry_delay_seconds(attempt: int) -> int:
    """Exponential backoff, capped.

    Attempt 1 waits 30s, attempt 2 waits 60s, attempt 3 waits 120s. Backing off
    matters when the cause is GitHub rate limiting or an overloaded API:
    retrying immediately makes the problem worse for everyone.
    """
    settings = get_settings()
    delay: int = settings.celery_retry_backoff_seconds * (2 ** max(0, attempt - 1))
    return min(delay, settings.celery_retry_backoff_max_seconds)


def compute_retry_delay(error: Exception, attempt: int) -> int:
    """How long to wait before attempting this job again.

    Split out from the task so the policy is testable without a Celery request
    context. When GitHub supplied a `Retry-After` it is used verbatim: GitHub
    knows exactly how long the limit lasts, and guessing shorter only wastes
    another request against the same exhausted budget.
    """
    if isinstance(error, GitHubRateLimitError | LLMRateLimitError | EmbeddingRateLimitError):
        return error.retry_after_seconds
    return _retry_delay_seconds(attempt)


@celery_app.task(
    name=REVIEW_TASK_NAME,
    bind=True,
    # Retries are scheduled explicitly below so the job row and the Celery retry
    # stay in step; `autoretry_for` would retry without recording anything.
    max_retries=None,
)
def review_pull_request(self: Task, review_job_id: str) -> dict[str, Any]:
    """Run the review pipeline for one queued job.

    Returns a small summary for the Celery result backend. The authoritative
    outcome is the `review_jobs` row, which is why this returns a description
    rather than data anything depends on.
    """
    job_id = uuid.UUID(review_job_id)

    with session_scope() as session:
        try:
            job = review_jobs.claim(session, job_id)
        except JobNotClaimableError:
            # Delivered twice, cancelled by a newer push, or already running
            # elsewhere. Not an error -- the correct action is to do nothing.
            logger.info("review_task.not_claimable", review_job_id=review_job_id)
            return {"review_job_id": review_job_id, "outcome": "skipped"}

        pull_request = session.get(PullRequest, job.pull_request_id)
        repository = session.get(Repository, pull_request.repository_id) if pull_request else None

        if pull_request is None or repository is None:
            # Defensive. Deleting a pull request cascades to its review jobs, so
            # a claimed job normally cannot outlive its parent -- but a job that
            # somehow does must fail with a reason rather than raise.
            error = review_pipeline.PipelineError(
                "The pull request or repository was deleted before the review ran."
            )
            review_jobs.mark_failed(session, job, error)
            return {"review_job_id": review_job_id, "outcome": "failed"}

        from app.integrations.github.factory import build_github_client

        client = build_github_client(get_settings())
        try:
            review_pipeline.execute_review(
                session,
                job=job,
                pull_request=pull_request,
                repository=repository,
                client=client,
            )
        except TRANSIENT_ERRORS as exc:
            return _handle_transient(self, session, job, exc, review_job_id)
        except SoftTimeLimitExceeded as exc:
            # The soft limit fires as an exception precisely so the reason can be
            # recorded before the hard limit kills the process.
            review_jobs.mark_failed(session, job, exc)
            logger.warning("review_task.timed_out", review_job_id=review_job_id)
            return {"review_job_id": review_job_id, "outcome": "failed"}
        except Exception as exc:
            # Permanent: a bug, bad data, or an unimplemented stage. Recorded
            # once with its traceback rather than retried into the same wall.
            review_jobs.mark_failed(session, job, exc)
            return {"review_job_id": review_job_id, "outcome": "failed"}
        finally:
            client.close()

        review_jobs.mark_succeeded(session, job)
        return {"review_job_id": review_job_id, "outcome": "succeeded"}


def _handle_transient(
    task: Task,
    session: Any,
    job: Any,
    error: Exception,
    review_job_id: str,
) -> dict[str, Any]:
    """Retry a transient failure, or give up once the budget is spent."""
    if not job.can_retry:
        review_jobs.mark_failed(session, job, error)
        logger.warning(
            "review_task.retries_exhausted",
            review_job_id=review_job_id,
            attempts=job.attempts,
        )
        return {"review_job_id": review_job_id, "outcome": "failed"}

    review_jobs.mark_for_retry(session, job, error)

    delay = compute_retry_delay(error, job.attempts)

    # Commit the queued state before Celery raises, so the row and the broker
    # agree even though `Retry` unwinds out of this function.
    session.commit()
    raise task.retry(exc=error, countdown=delay)


@celery_app.task(name=INDEX_TASK_NAME, bind=True, max_retries=None)
def index_repository_task(self: Task, repository_id: str) -> dict[str, Any]:
    """Index one repository at its default branch.

    Indexing is slow -- a tree listing, a file fetch per path and an embedding
    call per batch -- so it belongs on the worker for the same reason reviews
    do: an HTTP request that does this times out.
    """
    from app.db.models.repository import Repository
    from app.integrations.embeddings.factory import build_embedding_provider
    from app.integrations.github.factory import build_github_client
    from app.services.indexing import index_repository

    settings = get_settings()

    with session_scope() as session:
        repository = session.get(Repository, uuid.UUID(repository_id))
        if repository is None or not repository.is_active:
            logger.info("index_task.skipped", repository_id=repository_id)
            return {"repository_id": repository_id, "outcome": "skipped"}

        client = build_github_client(settings)
        try:
            result = index_repository(
                session,
                repository=repository,
                commit_sha=repository.default_branch,
                client=client,
                embedder=build_embedding_provider(settings),
                settings=settings,
            )
        except TRANSIENT_ERRORS as exc:
            logger.warning("index_task.transient_failure", error=str(exc))
            delay = compute_retry_delay(exc, self.request.retries + 1)
            raise self.retry(exc=exc, countdown=delay) from exc
        except Exception:
            logger.exception("index_task.failed", repository_id=repository_id)
            raise
        finally:
            client.close()

        return {
            "repository_id": repository_id,
            "outcome": "indexed",
            "chunks_created": result.chunks_created,
            "chunks_reused": result.chunks_reused,
            "chunks_removed": result.chunks_removed,
        }
