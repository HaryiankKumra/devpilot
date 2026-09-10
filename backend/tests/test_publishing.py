"""Tests for posting reviews back to GitHub.

This is the only code that writes to someone else's repository, so the negative
cases matter most: not posting twice, not posting guesses, and not losing a
stored review because a comment could not be delivered.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.enums import FindingCategory, FindingSeverity
from app.db.models.finding import Finding
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.models.review import Review
from app.db.models.review_job import ReviewJob
from app.db.models.user import User
from app.integrations.github.exceptions import GitHubAuthenticationError
from app.integrations.github.mock import (
    MOCK_INSTALLATION_ID,
    MOCK_PULL_REQUEST_REPOSITORY,
    MockGitHubClient,
)
from app.integrations.github.models import PostedReview
from app.services.publishing import (
    REVIEW_HEADER,
    publish_review,
    render_comment,
    render_summary,
)

HEAD_SHA = "c" * 40


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, post_reviews_to_github=True)


@pytest.fixture
def repository(db_session: Session, registered_user: User) -> Repository:
    row = Repository(
        owner_id=registered_user.id,
        github_repo_id=900_000_001,
        full_name=MOCK_PULL_REQUEST_REPOSITORY,
        installation_id=MOCK_INSTALLATION_ID,
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def pull_request(db_session: Session, repository: Repository) -> PullRequest:
    row = PullRequest(
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
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def review(db_session: Session, pull_request: PullRequest) -> Review:
    job = ReviewJob(pull_request_id=pull_request.id, head_sha=HEAD_SHA)
    db_session.add(job)
    db_session.flush()

    row = Review(
        review_job_id=job.id,
        pull_request_id=pull_request.id,
        summary="The change adds coupon handling.",
        risk_score=60,
        head_sha=HEAD_SHA,
        model_name="test-model",
    )
    db_session.add(row)
    db_session.flush()
    return row


def add_finding(
    db_session: Session,
    review: Review,
    *,
    confidence: float = 0.9,
    line: int | None = 7,
    severity: FindingSeverity = FindingSeverity.HIGH,
) -> Finding:
    finding = Finding(
        review_id=review.id,
        category=FindingCategory.BUG,
        severity=severity,
        file_path="checkout/coupons.py",
        line=line,
        title="Undefined name",
        description="LOOKUP is never defined.",
        suggestion="Import it from checkout.discounts.",
        confidence=confidence,
    )
    db_session.add(finding)
    db_session.flush()
    return finding


class TestPosting:
    def test_posts_high_confidence_findings(
        self,
        db_session: Session,
        review: Review,
        pull_request: PullRequest,
        repository: Repository,
        settings: Settings,
    ) -> None:
        add_finding(db_session, review, confidence=0.95)
        client = MockGitHubClient()

        result = publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=client,
            settings=settings,
        )

        assert result.posted is True
        assert result.comment_count == 1
        assert len(client.posted_reviews) == 1

    def test_pins_the_review_to_the_reviewed_commit(
        self,
        db_session: Session,
        review: Review,
        pull_request: PullRequest,
        repository: Repository,
        settings: Settings,
    ) -> None:
        """Without a commit id GitHub attaches comments to the branch head,
        which may already have moved past the code that was reviewed."""
        add_finding(db_session, review)
        client = MockGitHubClient()

        publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=client,
            settings=settings,
        )

        assert client.posted_reviews[0]["commit_sha"] == HEAD_SHA

    def test_sends_one_review_not_many_comments(
        self,
        db_session: Session,
        review: Review,
        pull_request: PullRequest,
        repository: Repository,
        settings: Settings,
    ) -> None:
        """One notification instead of a dozen is the difference between a tool
        people keep installed and one they mute."""
        for line in (2, 7, 8):
            add_finding(db_session, review, line=line)
        client = MockGitHubClient()

        publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=client,
            settings=settings,
        )

        assert len(client.posted_reviews) == 1
        assert len(client.posted_reviews[0]["comments"]) == 3

    def test_records_the_github_review_id(
        self,
        db_session: Session,
        review: Review,
        pull_request: PullRequest,
        repository: Repository,
        settings: Settings,
    ) -> None:
        add_finding(db_session, review)

        result = publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=MockGitHubClient(),
            settings=settings,
        )

        assert review.github_review_id == result.github_review_id


class TestIdempotency:
    def test_a_second_publish_posts_nothing(
        self,
        db_session: Session,
        review: Review,
        pull_request: PullRequest,
        repository: Repository,
        settings: Settings,
    ) -> None:
        """A retried job or redelivered webhook must not duplicate comments."""
        add_finding(db_session, review)
        client = MockGitHubClient()

        first = publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=client,
            settings=settings,
        )
        second = publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=client,
            settings=settings,
        )

        assert first.posted is True
        assert second.posted is False
        assert len(client.posted_reviews) == 1

    def test_findings_are_marked_posted(
        self,
        db_session: Session,
        review: Review,
        pull_request: PullRequest,
        repository: Repository,
        settings: Settings,
    ) -> None:
        add_finding(db_session, review)

        publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=MockGitHubClient(),
            settings=settings,
        )

        stored = db_session.execute(select(Finding)).scalar_one()
        assert stored.is_posted is True
        assert stored.github_comment_id is not None


class TestWhatIsWithheld:
    def test_low_confidence_findings_are_not_posted(
        self,
        db_session: Session,
        review: Review,
        pull_request: PullRequest,
        repository: Repository,
        settings: Settings,
    ) -> None:
        add_finding(db_session, review, confidence=0.3)
        client = MockGitHubClient()

        result = publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=client,
            settings=settings,
        )

        assert result.posted is False
        assert client.posted_reviews == []

    def test_low_confidence_findings_are_still_stored(
        self,
        db_session: Session,
        review: Review,
        pull_request: PullRequest,
        repository: Repository,
        settings: Settings,
    ) -> None:
        """Withheld from GitHub, kept on the dashboard."""
        add_finding(db_session, review, confidence=0.3)

        publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=MockGitHubClient(),
            settings=settings,
        )

        stored = db_session.execute(select(Finding)).scalar_one()
        assert stored.is_posted is False

    def test_file_level_findings_are_not_posted(
        self,
        db_session: Session,
        review: Review,
        pull_request: PullRequest,
        repository: Repository,
        settings: Settings,
    ) -> None:
        """There is no line for an inline comment to attach to."""
        add_finding(db_session, review, line=None)

        result = publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=MockGitHubClient(),
            settings=settings,
        )

        assert result.posted is False

    def test_nothing_is_posted_when_posting_is_disabled(
        self,
        db_session: Session,
        review: Review,
        pull_request: PullRequest,
        repository: Repository,
    ) -> None:
        """A misconfigured deployment should be silent, not chatty on somebody's
        pull request."""
        add_finding(db_session, review)
        client = MockGitHubClient()

        result = publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=client,
            settings=Settings(_env_file=None, post_reviews_to_github=False),
        )

        assert result.posted is False
        assert "disabled" in (result.skipped_reason or "")
        assert client.posted_reviews == []


class TestFailureHandling:
    def test_a_rejected_review_does_not_raise(
        self,
        db_session: Session,
        review: Review,
        pull_request: PullRequest,
        repository: Repository,
        settings: Settings,
    ) -> None:
        """The review is already stored; losing it because a comment could not
        be delivered would trade the valuable thing for the cosmetic one."""
        add_finding(db_session, review)

        result = publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=_RefusingClient(),
            settings=settings,
        )

        assert result.posted is False
        assert "rejected" in (result.skipped_reason or "")

    def test_findings_stay_unposted_after_a_failure(
        self,
        db_session: Session,
        review: Review,
        pull_request: PullRequest,
        repository: Repository,
        settings: Settings,
    ) -> None:
        """So a later retry can post them, rather than believing it already did."""
        add_finding(db_session, review)

        publish_review(
            db_session,
            review=review,
            pull_request=pull_request,
            repository=repository,
            client=_RefusingClient(),
            settings=settings,
        )

        assert db_session.execute(select(Finding)).scalar_one().is_posted is False


class TestRendering:
    def test_the_summary_says_the_score_is_computed(self, review: Review) -> None:
        """An automated reviewer that explains its own limits is easier to trust
        than one presenting itself as an oracle."""
        body = render_summary(review, finding_count=2)

        assert REVIEW_HEADER in body
        assert "60/100" in body
        assert "not chosen by the model" in body

    def test_the_summary_mentions_withheld_findings(self, review: Review) -> None:
        assert "dashboard" in render_summary(review, finding_count=1)

    def test_a_comment_states_severity_and_confidence(
        self, db_session: Session, review: Review
    ) -> None:
        """A reader deciding whether to act needs to know how sure the tool was."""
        finding = add_finding(db_session, review, confidence=0.82)

        body = render_comment(finding)

        assert "High" in body
        assert "bug" in body
        assert "82%" in body
        assert "Undefined name" in body

    def test_a_comment_includes_the_suggestion(self, db_session: Session, review: Review) -> None:
        finding = add_finding(db_session, review)

        assert "Import it from checkout.discounts." in render_comment(finding)


class _RefusingClient(MockGitHubClient):
    """A client whose review posting always fails.

    Subclasses the mock so it satisfies the full `GitHubClient` protocol while
    overriding only the one call under test.
    """

    def create_pull_request_review(self, *args: object, **kwargs: object) -> PostedReview:
        raise GitHubAuthenticationError("Resource not accessible by integration")
