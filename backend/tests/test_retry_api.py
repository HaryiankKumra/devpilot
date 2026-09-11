"""Tests for `POST /pull-requests/{id}/retry`.

The service rule is covered in `test_review_jobs.py`; this pins the HTTP
contract -- status codes, ownership, and that a retry actually reaches the
dispatcher rather than only writing a row.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.enums import ReviewJobStatus
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.models.review_job import ReviewJob
from app.db.models.user import User

SHA = "c" * 40


@pytest.fixture
def failed_job(db_session: Session, registered_user: User) -> ReviewJob:
    """A pull request whose only attempt failed on a transient provider error."""
    repository = Repository(
        owner_id=registered_user.id, github_repo_id=77, full_name="devpilot-demo/api"
    )
    db_session.add(repository)
    db_session.flush()
    pull_request = PullRequest(
        repository_id=repository.id,
        github_pr_id=770,
        number=7,
        title="Add retries",
        author_login="demo-developer",
        head_sha=SHA,
        base_sha="b" * 40,
        head_ref="feature/retries",
        base_ref="main",
    )
    db_session.add(pull_request)
    db_session.flush()
    job = ReviewJob(
        pull_request_id=pull_request.id,
        head_sha=SHA,
        status=ReviewJobStatus.FAILED,
        attempts=3,
        error_type="LLMTransientError",
        error_message="Gemini returned a server error: 503",
    )
    db_session.add(job)
    db_session.commit()
    return job


def retry_url(job: ReviewJob) -> str:
    return f"/api/v1/pull-requests/{job.pull_request_id}/retry"


class TestRetryEndpoint:
    def test_queues_a_new_attempt_and_dispatches_it(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        failed_job: ReviewJob,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        dispatched: list[uuid.UUID] = []

        def fake_dispatch(job_id: uuid.UUID) -> str:
            dispatched.append(job_id)
            return "celery-task-123"

        monkeypatch.setattr("app.api.v1.reviews.dispatch_review_job", fake_dispatch)

        response = client.post(retry_url(failed_job), headers=auth_headers)

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "queued"
        assert body["id"] != str(failed_job.id)
        assert body["head_sha"] == SHA
        # The row alone is not a retry; the worker has to hear about it.
        assert dispatched == [uuid.UUID(body["id"])]

    def test_records_the_celery_task_id(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        failed_job: ReviewJob,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """So a row can be traced to worker logs, same as a webhook-queued job."""
        monkeypatch.setattr("app.api.v1.reviews.dispatch_review_job", lambda _: "celery-task-123")

        body = client.post(retry_url(failed_job), headers=auth_headers).json()

        fresh = db_session.get(ReviewJob, uuid.UUID(body["id"]))
        assert fresh is not None
        assert fresh.celery_task_id == "celery-task-123"

    def test_refuses_when_the_latest_attempt_succeeded(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        failed_job: ReviewJob,
        db_session: Session,
    ) -> None:
        failed_job.status = ReviewJobStatus.SUCCEEDED
        db_session.commit()

        response = client.post(retry_url(failed_job), headers=auth_headers)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_someone_elses_pull_request_is_not_found(
        self, client: TestClient, failed_job: ReviewJob, db_session: Session
    ) -> None:
        from app.services.auth import register_user
        from tests.conftest import TEST_PASSWORD

        register_user(
            db_session, email="stranger@example.com", password=TEST_PASSWORD, full_name=None
        )
        db_session.commit()
        token = client.post(
            "/api/v1/auth/login", json={"email": "stranger@example.com", "password": TEST_PASSWORD}
        ).json()["access_token"]

        response = client.post(retry_url(failed_job), headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 404

    def test_requires_authentication(self, client: TestClient, failed_job: ReviewJob) -> None:
        assert client.post(retry_url(failed_job)).status_code == 401
