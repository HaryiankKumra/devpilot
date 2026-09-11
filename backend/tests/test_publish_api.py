"""Tests for `POST /reviews/{id}/publish`.

The publishing rules themselves are covered in `test_publishing.py`; this pins
the HTTP contract for re-posting a review that exists but never reached GitHub.

Found necessary on the first real pull request: the App had read-only access,
the post got 403, the review was stored (a failed post deliberately does not
fail the job), and there was no way to send it once the permission was fixed.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Environment, Settings
from app.core.enums import FindingCategory, FindingSeverity
from app.db.models.finding import Finding
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.models.review import Review
from app.db.models.review_job import ReviewJob
from app.db.models.user import User
from app.integrations.github.mock import MOCK_INSTALLATION_ID, MOCK_PULL_REQUEST_REPOSITORY
from tests.conftest import TEST_PASSWORD

HEAD_SHA = "d" * 40


@pytest.fixture
def settings() -> Settings:
    """Posting is off by default; this endpoint exists to post."""
    return Settings(
        _env_file=None,
        environment=Environment.CI,
        rate_limit_enabled=False,
        post_reviews_to_github=True,
    )


@pytest.fixture
def stored_review(db_session: Session, registered_user: User) -> Review:
    """A completed review on the mock repository, not yet posted anywhere."""
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
        github_pr_id=800_000_001,
        number=42,
        title="Add coupon validation",
        author_login="demo-developer",
        head_sha=HEAD_SHA,
        base_sha="b" * 40,
        head_ref="feature",
        base_ref="main",
    )
    db_session.add(pull_request)
    db_session.flush()
    job = ReviewJob(pull_request_id=pull_request.id, head_sha=HEAD_SHA)
    db_session.add(job)
    db_session.flush()
    review = Review(
        review_job_id=job.id,
        pull_request_id=pull_request.id,
        summary="The change adds coupon handling.",
        risk_score=60,
        head_sha=HEAD_SHA,
        model_name="test-model",
    )
    db_session.add(review)
    db_session.commit()
    return review


def add_finding(db_session: Session, review: Review, *, confidence: float) -> Finding:
    finding = Finding(
        review_id=review.id,
        category=FindingCategory.BUG,
        severity=FindingSeverity.HIGH,
        file_path="checkout/coupons.py",
        line=7,
        title="Undefined name",
        description="LOOKUP is never defined.",
        confidence=confidence,
    )
    db_session.add(finding)
    db_session.commit()
    return finding


def url(review: Review) -> str:
    return f"/api/v1/reviews/{review.id}/publish"


class TestPublishEndpoint:
    def test_posts_a_stored_review(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        stored_review: Review,
    ) -> None:
        add_finding(db_session, stored_review, confidence=0.9)

        response = client.post(url(stored_review), headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["posted"] is True
        assert body["comment_count"] == 1
        assert body["github_review_id"] is not None
        assert body["html_url"].startswith("https://github.com/")

    def test_marks_the_review_and_findings_as_posted(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        stored_review: Review,
    ) -> None:
        finding = add_finding(db_session, stored_review, confidence=0.9)

        client.post(url(stored_review), headers=auth_headers)

        db_session.refresh(stored_review)
        db_session.refresh(finding)
        assert stored_review.github_review_id is not None
        assert finding.is_posted is True

    def test_is_idempotent(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        stored_review: Review,
    ) -> None:
        """A second click must not put a second review on the pull request."""
        add_finding(db_session, stored_review, confidence=0.9)
        first = client.post(url(stored_review), headers=auth_headers).json()

        second = client.post(url(stored_review), headers=auth_headers).json()

        assert first["posted"] is True
        assert second["posted"] is False
        assert second["skipped_reason"]

    def test_explains_when_nothing_is_worth_posting(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        stored_review: Review,
    ) -> None:
        add_finding(db_session, stored_review, confidence=0.3)  # below the threshold

        body = client.post(url(stored_review), headers=auth_headers).json()

        assert body["posted"] is False
        assert body["skipped_reason"]

    def test_someone_elses_review_is_not_found(
        self, client: TestClient, db_session: Session, stored_review: Review
    ) -> None:
        """404, not 403: confirming it exists is itself information."""
        from app.services.auth import register_user

        register_user(
            db_session, email="stranger@example.com", password=TEST_PASSWORD, full_name=None
        )
        db_session.commit()
        token = client.post(
            "/api/v1/auth/login",
            json={"email": "stranger@example.com", "password": TEST_PASSWORD},
        ).json()["access_token"]

        response = client.post(url(stored_review), headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 404

    def test_an_unknown_review_is_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(f"/api/v1/reviews/{uuid.uuid4()}/publish", headers=auth_headers)

        assert response.status_code == 404

    def test_requires_authentication(self, client: TestClient, stored_review: Review) -> None:
        assert client.post(url(stored_review)).status_code == 401
