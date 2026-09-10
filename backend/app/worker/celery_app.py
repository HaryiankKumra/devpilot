"""The Celery application.

Configuration here is mostly about **not losing work**, which is the whole point
of moving reviews off the request path in the first place.

The two settings that matter most:

`acks_late=True` means a task is acknowledged after it finishes, not when the
worker picks it up. If a worker is killed mid-review -- a deploy, an OOM, a lost
spot instance -- the broker never saw an ack and redelivers the task. The default
(`acks_late=False`) acknowledges on receipt, so that same crash silently loses
the job. The cost is that a task can be delivered twice, which is why claiming a
job is a conditional UPDATE rather than a read-then-write.

`worker_prefetch_multiplier=1` stops a worker reserving a batch of tasks it has
not started. Reviews are long and uneven, so a prefetched queue leaves jobs
waiting behind one slow review while another worker sits idle.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

# Queue names. Splitting them now costs nothing and means indexing work
# (Milestone 8) can be given its own workers later without a code change.
REVIEW_QUEUE = "reviews"


def create_celery_app(settings: Settings | None = None) -> Celery:
    """Build the Celery application."""
    settings = settings or get_settings()
    configure_logging(settings)

    # `include` rather than `autodiscover_tasks`: Celery imports these modules
    # when the app is finalised, which is after `celery_app` has been bound at
    # the bottom of this file. Autodiscovering here instead imports
    # `app.worker.tasks` while this function is still running, and that module
    # imports `celery_app` back -- a circular import that only surfaces when a
    # worker boots, never in tests that import `tasks` directly.
    app = Celery("devpilot", include=["app.worker.tasks"])

    app.conf.update(
        broker_url=settings.celery_broker_url,
        result_backend=settings.celery_result_backend,
        # --- serialisation ---
        # JSON only. Celery's older `pickle` default executes arbitrary code
        # from the broker, so anyone who can write to Redis gets remote code
        # execution on every worker.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # --- reliability ---
        task_acks_late=True,
        # Redeliver if the worker process dies rather than exiting cleanly.
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=settings.celery_prefetch_multiplier,
        task_soft_time_limit=settings.celery_task_soft_time_limit_seconds,
        task_time_limit=settings.celery_task_time_limit_seconds,
        # Results are for debugging, not for correctness: the database is the
        # source of truth for a job's state. Expire them so Redis does not grow.
        result_expires=3600,
        # --- routing ---
        task_default_queue=REVIEW_QUEUE,
        task_routes={"devpilot.review_pull_request": {"queue": REVIEW_QUEUE}},
        # --- observability ---
        # Without these, a task in flight is invisible in `celery inspect`.
        task_track_started=True,
        task_send_sent_event=True,
        worker_send_task_events=True,
        timezone="UTC",
        enable_utc=True,
    )

    return app


celery_app = create_celery_app()
