"""Tests for the ORM models: constraints, cascades and stored representations.

These exercise the guarantees the database is supposed to enforce, rather than
the ones application code merely intends to. A check constraint that was never
tested is a check constraint that may not exist.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.enums import (
    FindingCategory,
    FindingSeverity,
    PullRequestState,
    ReviewJobStatus,
)
from app.db.models.finding import Finding
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.models.review import Review
from app.db.models.review_job import ReviewJob
from app.db.models.user import User
from app.db.models.webhook_event import WebhookEvent

SHA_A = "a" * 40
SHA_B = "b" * 40


def make_user(session: Session, email: str = "owner@example.com") -> User:
    user = User(email=email, hashed_password="$argon2id$fake")
    session.add(user)
    session.flush()
    return user


def make_repository(session: Session, user: User, github_repo_id: int = 1) -> Repository:
    repo = Repository(owner_id=user.id, github_repo_id=github_repo_id, full_name="octocat/hello")
    session.add(repo)
    session.flush()
    return repo


def make_pull_request(
    session: Session, repo: Repository, number: int = 1, github_pr_id: int = 100
) -> PullRequest:
    pr = PullRequest(
        repository_id=repo.id,
        github_pr_id=github_pr_id,
        number=number,
        title="Add a feature",
        author_login="octocat",
        head_sha=SHA_A,
        base_sha=SHA_B,
        head_ref="feature",
        base_ref="main",
    )
    session.add(pr)
    session.flush()
    return pr


def make_review(session: Session, pr: PullRequest, risk_score: int = 40) -> Review:
    job = ReviewJob(pull_request_id=pr.id, head_sha=SHA_A)
    session.add(job)
    session.flush()
    review = Review(
        review_job_id=job.id,
        pull_request_id=pr.id,
        summary="Looks mostly fine.",
        risk_score=risk_score,
        head_sha=SHA_A,
        model_name="test-model",
    )
    session.add(review)
    session.flush()
    return review


class TestUser:
    def test_email_must_be_unique(self, db_session: Session) -> None:
        make_user(db_session, "taken@example.com")

        with pytest.raises(IntegrityError):
            make_user(db_session, "taken@example.com")

    def test_defaults_to_active(self, db_session: Session) -> None:
        assert make_user(db_session).is_active is True

    def test_timestamps_are_populated_by_the_database(self, db_session: Session) -> None:
        user = make_user(db_session)
        db_session.commit()

        assert user.created_at is not None
        assert user.updated_at is not None


class TestRepository:
    def test_github_repo_id_must_be_unique(self, db_session: Session) -> None:
        """GitHub's id is the identity that survives a rename, so it is the key."""
        user = make_user(db_session)
        make_repository(db_session, user, github_repo_id=42)

        with pytest.raises(IntegrityError):
            make_repository(db_session, user, github_repo_id=42)

    def test_deleting_a_user_deletes_their_repositories(self, db_session: Session) -> None:
        user = make_user(db_session)
        make_repository(db_session, user)
        db_session.commit()

        db_session.delete(user)
        db_session.commit()

        assert db_session.execute(select(Repository)).first() is None


class TestPullRequest:
    def test_number_is_unique_within_a_repository(self, db_session: Session) -> None:
        user = make_user(db_session)
        repo = make_repository(db_session, user)
        make_pull_request(db_session, repo, number=7, github_pr_id=1)

        with pytest.raises(IntegrityError):
            make_pull_request(db_session, repo, number=7, github_pr_id=2)

    def test_the_same_number_may_exist_in_another_repository(self, db_session: Session) -> None:
        user = make_user(db_session)
        first = make_repository(db_session, user, github_repo_id=1)
        second = make_repository(db_session, user, github_repo_id=2)

        make_pull_request(db_session, first, number=7, github_pr_id=1)
        make_pull_request(db_session, second, number=7, github_pr_id=2)

        assert len(db_session.execute(select(PullRequest)).all()) == 2

    def test_state_is_stored_as_its_value_not_its_python_name(self, db_session: Session) -> None:
        """Storing `OPEN` instead of `open` would break every existing row if a
        member were ever renamed, and is not what the API speaks."""
        user = make_user(db_session)
        repo = make_repository(db_session, user)
        make_pull_request(db_session, repo)
        db_session.commit()

        stored = db_session.execute(text("SELECT state FROM pull_requests")).scalar_one()
        assert stored == "open"
        assert stored == PullRequestState.OPEN.value


class TestReviewJob:
    def test_starts_queued_with_no_attempts(self, db_session: Session) -> None:
        user = make_user(db_session)
        repo = make_repository(db_session, user)
        pr = make_pull_request(db_session, repo)

        job = ReviewJob(pull_request_id=pr.id, head_sha=SHA_A)
        db_session.add(job)
        db_session.flush()

        assert job.status is ReviewJobStatus.QUEUED
        assert job.attempts == 0

    @pytest.mark.parametrize(
        ("attempts", "expected"),
        [(0, True), (2, True), (3, False), (4, False)],
    )
    def test_can_retry_respects_the_attempt_budget(
        self, db_session: Session, attempts: int, expected: bool
    ) -> None:
        user = make_user(db_session)
        repo = make_repository(db_session, user)
        pr = make_pull_request(db_session, repo)

        job = ReviewJob(pull_request_id=pr.id, head_sha=SHA_A, attempts=attempts)
        db_session.add(job)
        db_session.flush()

        assert job.can_retry is expected


class TestReview:
    @pytest.mark.parametrize("risk_score", [-1, 101])
    def test_rejects_a_risk_score_outside_zero_to_one_hundred(
        self, db_session: Session, risk_score: int
    ) -> None:
        """The score is computed, so an out-of-range value means a scoring bug."""
        user = make_user(db_session)
        repo = make_repository(db_session, user)
        pr = make_pull_request(db_session, repo)

        with pytest.raises(IntegrityError):
            make_review(db_session, pr, risk_score=risk_score)

    @pytest.mark.parametrize("risk_score", [0, 50, 100])
    def test_accepts_scores_at_and_inside_the_bounds(
        self, db_session: Session, risk_score: int
    ) -> None:
        user = make_user(db_session)
        repo = make_repository(db_session, user)
        pr = make_pull_request(db_session, repo)

        assert make_review(db_session, pr, risk_score=risk_score).risk_score == risk_score

    def test_one_review_per_job(self, db_session: Session) -> None:
        user = make_user(db_session)
        repo = make_repository(db_session, user)
        pr = make_pull_request(db_session, repo)
        review = make_review(db_session, pr)

        duplicate = Review(
            review_job_id=review.review_job_id,
            pull_request_id=pr.id,
            summary="A second review of the same attempt.",
            risk_score=10,
            head_sha=SHA_A,
            model_name="test-model",
        )
        db_session.add(duplicate)

        with pytest.raises(IntegrityError):
            db_session.flush()


class TestFinding:
    def _finding(self, review: Review, **overrides: object) -> Finding:
        defaults: dict[str, object] = {
            "review_id": review.id,
            "category": FindingCategory.SECURITY,
            "severity": FindingSeverity.HIGH,
            "file_path": "app/main.py",
            "line": 12,
            "title": "Unvalidated input",
            "description": "The value reaches the query unescaped.",
            "confidence": 0.9,
        }
        return Finding(**{**defaults, **overrides})

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_rejects_confidence_outside_zero_to_one(
        self, db_session: Session, confidence: float
    ) -> None:
        user = make_user(db_session)
        repo = make_repository(db_session, user)
        review = make_review(db_session, make_pull_request(db_session, repo))

        db_session.add(self._finding(review, confidence=confidence))

        with pytest.raises(IntegrityError):
            db_session.flush()

    @pytest.mark.parametrize("line", [0, -5])
    def test_rejects_a_non_positive_line_number(self, db_session: Session, line: int) -> None:
        """Line numbers are 1-based; anything else means the model invented one."""
        user = make_user(db_session)
        repo = make_repository(db_session, user)
        review = make_review(db_session, make_pull_request(db_session, repo))

        db_session.add(self._finding(review, line=line))

        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_allows_a_finding_with_no_line(self, db_session: Session) -> None:
        """A finding can concern a whole file rather than one line."""
        user = make_user(db_session)
        repo = make_repository(db_session, user)
        review = make_review(db_session, make_pull_request(db_session, repo))

        db_session.add(self._finding(review, line=None))
        db_session.flush()

        assert db_session.execute(select(Finding)).scalar_one().line is None

    def test_deleting_a_review_deletes_its_findings(self, db_session: Session) -> None:
        user = make_user(db_session)
        repo = make_repository(db_session, user)
        review = make_review(db_session, make_pull_request(db_session, repo))
        db_session.add(self._finding(review))
        db_session.commit()

        db_session.delete(review)
        db_session.commit()

        assert db_session.execute(select(Finding)).first() is None

    def test_defaults_to_not_posted(self, db_session: Session) -> None:
        user = make_user(db_session)
        repo = make_repository(db_session, user)
        review = make_review(db_session, make_pull_request(db_session, repo))

        finding = self._finding(review)
        db_session.add(finding)
        db_session.flush()

        assert finding.is_posted is False


class TestWebhookEvent:
    def test_delivery_id_must_be_unique(self, db_session: Session) -> None:
        """This single constraint is the webhook idempotency guarantee."""
        for _ in range(2):
            db_session.add(
                WebhookEvent(
                    delivery_id="delivery-abc-123",
                    event_type="pull_request",
                    action="opened",
                    payload={"number": 1},
                )
            )

        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_stores_an_arbitrary_json_payload(self, db_session: Session) -> None:
        payload = {"action": "opened", "pull_request": {"number": 7, "labels": []}}
        event = WebhookEvent(
            delivery_id=str(uuid.uuid4()), event_type="pull_request", payload=payload
        )
        db_session.add(event)
        db_session.commit()

        assert db_session.execute(select(WebhookEvent)).scalar_one().payload == payload

    def test_survives_an_unknown_repository(self, db_session: Session) -> None:
        """A delivery for a repository we have never seen must still be recorded,
        or its retry would not be recognised as a duplicate."""
        event = WebhookEvent(
            delivery_id=str(uuid.uuid4()),
            event_type="pull_request",
            repository_id=None,
            payload={},
        )
        db_session.add(event)
        db_session.flush()

        assert event.repository_id is None
