"""Handing queued jobs to the worker.

This module exists because of one ordering rule that is easy to get wrong:

**Enqueue after the transaction commits, never inside it.**

Redis and PostgreSQL are separate systems with no shared transaction. If a task
is published while the job row is still uncommitted, a worker can pick it up
before -- or instead of -- the commit landing, and look for a row that does not
exist. Publishing after the commit inverts the failure: a crash in the gap
leaves a job sitting in `queued` that no worker was told about, which a periodic
sweep can pick up. A lost row is corruption; a delayed job is a delay.

Dispatch failures are deliberately swallowed for the same reason. If Redis is
down when a webhook arrives, the job row is already safely committed, and
answering GitHub with a 500 would only earn a redelivery of work already
recorded.
"""

from __future__ import annotations

import uuid

from app.core.logging import get_logger

logger = get_logger(__name__)


def dispatch_review_job(review_job_id: uuid.UUID) -> str | None:
    """Publish a review task and return its Celery id, or `None` on failure.

    Imports the task lazily so that importing the API does not pull in Celery
    and open a broker connection -- the web process never needs one.
    """
    from app.worker.tasks import review_pull_request

    try:
        async_result = review_pull_request.delay(str(review_job_id))
    except Exception as exc:
        # Broker unreachable. The job row is committed and still `queued`, so
        # nothing is lost; it simply waits to be picked up.
        logger.error(
            "dispatch.failed",
            review_job_id=str(review_job_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None

    logger.info("dispatch.queued", review_job_id=str(review_job_id), celery_task_id=async_result.id)
    return str(async_result.id)


def dispatch_repository_index(repository_id: uuid.UUID) -> str | None:
    """Publish an indexing task. Same after-commit rule as review dispatch."""
    from app.worker.tasks import index_repository_task

    try:
        async_result = index_repository_task.delay(str(repository_id))
    except Exception as exc:
        logger.error(
            "dispatch.index_failed",
            repository_id=str(repository_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None

    logger.info(
        "dispatch.index_queued",
        repository_id=str(repository_id),
        celery_task_id=async_result.id,
    )
    return str(async_result.id)
