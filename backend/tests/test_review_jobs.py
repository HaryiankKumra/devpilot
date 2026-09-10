"""Tests for the review job state machine.

No broker and no worker here: the lifecycle is plain functions over a session,
which is exactly why it was kept out of the Celery task.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.enums import ReviewJobStatus
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.models.review_job import ReviewJob
from app.db.models.user import User
from app.services import review_jobs
from app.services.review_jobs import (
    MAX_ERROR_MESSAGE_CHARS,
    JobNotClaimableError,
)

SHA = "a" * 40


@pytest.fixture
def job(db_session: Session, registered_user: User) -> ReviewJob:
    """A queued job with the rows it depends on."""
    repository = Repository(
        owner_id=registered_user.id,
        github_repo_id=1,
        full_name="devpilot-demo/checkout-service",
    )
    db_session.add(repository)
    db_session.flush()

    pull_request = PullRequest(
        repository_id=repository.id,
        github_pr_id=10,
        number=1,
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


class TestClaim:
    def test_moves_a_queued_job_to_running(self, db_session: Session, job: ReviewJob) -> None:
        claimed = review_jobs.claim(db_session, job.id)

        assert claimed.status is ReviewJobStatus.RUNNING
        assert claimed.started_at is not None

    def test_counts_the_attempt(self, db_session: Session, job: ReviewJob) -> None:
        """Incremented on claim, not on failure, so a worker that dies without
        recording anything still consumes an attempt."""
        assert review_jobs.claim(db_session, job.id).attempts == 1

    def test_a_second_claim_is_refused(self, db_session: Session, job: ReviewJob) -> None:
        """Celery redelivers tasks when a worker dies, so two workers can hold
        the same task. Exactly one may run it."""
        review_jobs.claim(db_session, job.id)

        with pytest.raises(JobNotClaimableError):
            review_jobs.claim(db_session, job.id)

    def test_a_cancelled_job_cannot_be_claimed(self, db_session: Session, job: ReviewJob) -> None:
        """A newer push superseded it; reviewing the old commit is wasted work."""
        job.status = ReviewJobStatus.CANCELLED
        db_session.commit()

        with pytest.raises(JobNotClaimableError):
            review_jobs.claim(db_session, job.id)

    @pytest.mark.parametrize(
        "status",
        [ReviewJobStatus.RUNNING, ReviewJobStatus.SUCCEEDED, ReviewJobStatus.FAILED],
    )
    def test_only_queued_jobs_are_claimable(
        self, db_session: Session, job: ReviewJob, status: ReviewJobStatus
    ) -> None:
        job.status = status
        db_session.commit()

        with pytest.raises(JobNotClaimableError):
            review_jobs.claim(db_session, job.id)

    def test_an_unknown_id_is_refused(self, db_session: Session) -> None:
        with pytest.raises(JobNotClaimableError):
            review_jobs.claim(db_session, uuid.uuid4())

    def test_a_retried_job_can_be_claimed_again(self, db_session: Session, job: ReviewJob) -> None:
        review_jobs.claim(db_session, job.id)
        review_jobs.mark_for_retry(db_session, job, ConnectionError("network blip"))
        db_session.commit()

        second = review_jobs.claim(db_session, job.id)

        assert second.status is ReviewJobStatus.RUNNING
        assert second.attempts == 2


class TestMarkSucceeded:
    def test_records_completion(self, db_session: Session, job: ReviewJob) -> None:
        claimed = review_jobs.claim(db_session, job.id)

        review_jobs.mark_succeeded(db_session, claimed)

        assert claimed.status is ReviewJobStatus.SUCCEEDED
        assert claimed.finished_at is not None

    def test_clears_errors_from_earlier_attempts(self, db_session: Session, job: ReviewJob) -> None:
        """A job that failed twice then succeeded is a success, and should not
        still display the error that has since been overcome."""
        claimed = review_jobs.claim(db_session, job.id)
        review_jobs.mark_for_retry(db_session, claimed, ConnectionError("blip"))

        review_jobs.mark_succeeded(db_session, claimed)

        assert claimed.error_type is None
        assert claimed.error_message is None
        assert claimed.error_traceback is None


class TestMarkFailed:
    def test_records_the_error_in_three_parts(self, db_session: Session, job: ReviewJob) -> None:
        claimed = review_jobs.claim(db_session, job.id)

        try:
            raise ValueError("the diff could not be parsed")
        except ValueError as exc:
            review_jobs.mark_failed(db_session, claimed, exc)

        assert claimed.status is ReviewJobStatus.FAILED
        # Groupable, human-readable, and debuggable, respectively.
        assert claimed.error_type == "ValueError"
        assert claimed.error_message == "the diff could not be parsed"
        assert "ValueError" in (claimed.error_traceback or "")
        assert claimed.finished_at is not None

    def test_truncates_a_runaway_message(self, db_session: Session, job: ReviewJob) -> None:
        """A wedged loop can produce megabytes of error text; storing it all
        helps nobody."""
        claimed = review_jobs.claim(db_session, job.id)

        review_jobs.mark_failed(db_session, claimed, ValueError("x" * 10_000))

        assert claimed.error_message is not None
        assert len(claimed.error_message) == MAX_ERROR_MESSAGE_CHARS


class TestMarkForRetry:
    def test_returns_the_job_to_the_queue(self, db_session: Session, job: ReviewJob) -> None:
        claimed = review_jobs.claim(db_session, job.id)

        review_jobs.mark_for_retry(db_session, claimed, ConnectionError("blip"))

        assert claimed.status is ReviewJobStatus.QUEUED
        assert claimed.started_at is None

    def test_keeps_the_error_for_the_record(self, db_session: Session, job: ReviewJob) -> None:
        """If the job later exhausts its attempts, this is the only trace of
        what went wrong on the way there."""
        claimed = review_jobs.claim(db_session, job.id)

        review_jobs.mark_for_retry(db_session, claimed, ConnectionError("blip"))

        assert claimed.error_type == "ConnectionError"
        assert claimed.error_message == "blip"

    def test_does_not_set_a_finish_time(self, db_session: Session, job: ReviewJob) -> None:
        claimed = review_jobs.claim(db_session, job.id)

        review_jobs.mark_for_retry(db_session, claimed, ConnectionError("blip"))

        assert claimed.finished_at is None


class TestAttemptBudget:
    def test_can_retry_until_the_budget_is_spent(self, db_session: Session, job: ReviewJob) -> None:
        """`max_attempts` is 3, so three attempts are permitted and the fourth
        is refused."""
        assert job.max_attempts == 3

        for expected in (True, True, True, False):
            assert job.can_retry is expected
            job.attempts += 1
            db_session.flush()
