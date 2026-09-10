"""Tests for the Celery task and the dispatch seam.

The task runs **synchronously** here, called as a plain function with its
session factory pointed at the test database. That exercises the real task body
-- claiming, error classification, retry decisions -- without a broker or a
worker process, which would make these tests slow and flaky for no extra
coverage. What a real broker adds (serialisation, redelivery) is Celery's own
behaviour, not ours.

The production path always raises `PipelineNotImplementedError`, so the success
path is exercised by substituting a pipeline that returns instead.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from celery.exceptions import Retry, SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import ReviewJobStatus
from app.db.models.finding import Finding
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.models.review import Review
from app.db.models.review_job import ReviewJob
from app.db.models.user import User
from app.integrations.github.exceptions import GitHubRateLimitError, GitHubTransientError
from app.integrations.github.mock import (
    MOCK_INSTALLATION_ID,
    MOCK_PULL_REQUEST_REPOSITORY,
)
from app.services import review_pipeline
from app.services.review_pipeline import ReviewOutcome
from app.worker import tasks

SHA = "a" * 40


@pytest.fixture
def job(db_session: Session, registered_user: User) -> ReviewJob:
    # Matches the mock GitHub's repository and pull request, so the pipeline
    # stages added in Milestone 6 can fetch a real diff before reaching the
    # unimplemented LLM stage.
    repository = Repository(
        owner_id=registered_user.id,
        github_repo_id=900_000_001,
        full_name=MOCK_PULL_REQUEST_REPOSITORY,
        installation_id=MOCK_INSTALLATION_ID,
    )
    db_session.add(repository)
    db_session.flush()

    pull_request = PullRequest(
        repository_id=repository.id,
        github_pr_id=10,
        number=42,
        title="Add coupon validation",
        author_login="demo-developer",
        head_sha=SHA,
        base_sha="b" * 40,
        head_ref="feature",
        base_ref="main",
    )
    db_session.add(pull_request)
    db_session.flush()

    queued = ReviewJob(pull_request_id=pull_request.id, head_sha=SHA)
    db_session.add(queued)
    db_session.commit()
    return queued


@pytest.fixture(autouse=True)
def use_test_session(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    """Point the task's session factory at the test database.

    The task opens its own session because a worker has no request to inherit
    one from; here that factory yields the session the test already holds, so
    assertions see what the task wrote.
    """

    @contextmanager
    def scope() -> Iterator[Session]:
        yield db_session
        db_session.flush()

    monkeypatch.setattr(tasks, "session_scope", scope)


def run_task(job_id: uuid.UUID) -> dict[str, Any]:
    """Invoke the task body synchronously."""
    return tasks.review_pull_request.run(str(job_id))  # type: ignore[no-any-return]


class TestCompletedPipeline:
    """With the LLM stage in place, a job now runs to completion."""

    def test_the_job_succeeds(self, db_session: Session, job: ReviewJob) -> None:
        result = run_task(job.id)

        assert result["outcome"] == "succeeded"
        db_session.refresh(job)
        assert job.status is ReviewJobStatus.SUCCEEDED
        assert job.error_type is None

    def test_a_review_is_stored(self, db_session: Session, job: ReviewJob) -> None:
        run_task(job.id)

        stored = db_session.execute(select(Review)).scalar_one()
        assert stored.review_job_id == job.id
        assert 0 <= stored.risk_score <= 100

    def test_findings_are_stored(self, db_session: Session, job: ReviewJob) -> None:
        run_task(job.id)

        assert db_session.execute(select(Finding)).scalars().all()

    def test_it_runs_in_one_attempt(self, db_session: Session, job: ReviewJob) -> None:
        run_task(job.id)

        db_session.refresh(job)
        assert job.attempts == 1


class TestSuccessPath:
    def test_marks_the_job_succeeded(
        self, db_session: Session, job: ReviewJob, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            review_pipeline,
            "execute_review",
            lambda *args, **kwargs: ReviewOutcome(
                summary="Looks fine.", risk_score=12, model_name="test-model", finding_count=0
            ),
        )

        result = run_task(job.id)

        assert result["outcome"] == "succeeded"
        db_session.refresh(job)
        assert job.status is ReviewJobStatus.SUCCEEDED
        assert job.finished_at is not None
        assert job.error_type is None


class TestTransientFailures:
    """Called directly rather than through a worker, Celery's `retry()` re-raises
    the original exception instead of `Retry`. That is documented behaviour, so
    these assert on the *job row* -- which is what DevPilot controls and what a
    retry actually depends on."""

    @pytest.mark.parametrize(
        "error",
        [
            GitHubTransientError("GitHub returned 502"),
            ConnectionError("connection reset"),
            TimeoutError("read timed out"),
        ],
    )
    def test_return_the_job_to_the_queue(
        self,
        db_session: Session,
        job: ReviewJob,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
    ) -> None:
        monkeypatch.setattr(review_pipeline, "execute_review", _raiser(error))

        with pytest.raises((Retry, type(error))):
            run_task(job.id)

        db_session.refresh(job)
        assert job.status is ReviewJobStatus.QUEUED
        assert job.error_type == type(error).__name__
        assert job.finished_at is None

    def test_stop_once_the_attempt_budget_is_spent(
        self, db_session: Session, job: ReviewJob, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(review_pipeline, "execute_review", _raiser(GitHubTransientError("502")))
        job.attempts = job.max_attempts - 1  # the coming claim spends the last one
        db_session.commit()

        result = run_task(job.id)

        assert result["outcome"] == "failed"
        db_session.refresh(job)
        assert job.status is ReviewJobStatus.FAILED
        assert job.error_type == "GitHubTransientError"


class TestRetryDelay:
    def test_honours_githubs_own_retry_after(self) -> None:
        """GitHub knows exactly how long the limit lasts; guessing shorter just
        wastes another request against the same exhausted budget."""
        error = GitHubRateLimitError("rate limited", retry_after_seconds=137)

        assert tasks.compute_retry_delay(error, attempt=1) == 137

    def test_backs_off_exponentially_for_other_transient_errors(self) -> None:
        delays = [
            tasks.compute_retry_delay(GitHubTransientError("502"), attempt=n) for n in (1, 2, 3)
        ]

        assert delays == [30, 60, 120]

    def test_is_capped(self) -> None:
        """Unbounded doubling would eventually schedule a retry days away."""
        assert tasks.compute_retry_delay(ConnectionError("x"), attempt=20) == 600


class TestTimeout:
    def test_a_soft_timeout_is_recorded_before_the_hard_kill(
        self, db_session: Session, job: ReviewJob, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The soft limit arrives as an exception precisely so the reason can be
        written down before the process is killed."""
        monkeypatch.setattr(review_pipeline, "execute_review", _raiser(SoftTimeLimitExceeded()))

        result = run_task(job.id)

        assert result["outcome"] == "failed"
        db_session.refresh(job)
        assert job.error_type == "SoftTimeLimitExceeded"


class TestDeliveredTwice:
    def test_a_second_delivery_does_nothing(self, db_session: Session, job: ReviewJob) -> None:
        """`acks_late` means a task can legitimately arrive twice."""
        run_task(job.id)
        db_session.refresh(job)
        first_attempts = job.attempts

        result = run_task(job.id)

        assert result["outcome"] == "skipped"
        db_session.refresh(job)
        assert job.attempts == first_attempts

    def test_an_unknown_job_is_skipped_not_crashed(self, db_session: Session) -> None:
        assert run_task(uuid.uuid4())["outcome"] == "skipped"

    def test_a_cancelled_job_is_skipped(self, db_session: Session, job: ReviewJob) -> None:
        job.status = ReviewJobStatus.CANCELLED
        db_session.commit()

        assert run_task(job.id)["outcome"] == "skipped"


class TestDeletedParent:
    def test_deleting_a_pull_request_removes_its_jobs(
        self, db_session: Session, job: ReviewJob
    ) -> None:
        """The foreign key cascades, so a job cannot outlive its pull request.
        The task's guard for a missing parent is therefore belt-and-braces."""
        db_session.delete(db_session.get(PullRequest, job.pull_request_id))
        db_session.commit()
        # The session caches loaded rows, and the cascade happened in the
        # database rather than through the ORM, so the identity map still holds
        # a stale job. Drop it to see what the database actually contains.
        db_session.expunge_all()

        assert db_session.get(ReviewJob, job.id) is None
        assert run_task(job.id)["outcome"] == "skipped"


def _raiser(error: Exception) -> Any:
    def _raise(*args: object, **kwargs: object) -> None:
        raise error

    return _raise
