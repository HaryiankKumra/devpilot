"""Tests for the review pipeline stages that exist.

These run the real stages against the mock GitHub client, which serves a small
diff containing genuine defects -- so static analysis has something honest to
find rather than always returning an empty list.
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
from app.integrations.github.client import GitHubClient
from app.integrations.github.mock import (
    MOCK_INSTALLATION_ID,
    MOCK_PULL_REQUEST_REPOSITORY,
    MockGitHubClient,
)
from app.integrations.llm.schemas import LLMFinding, LLMReview, LLMReviewResponse
from app.services import review_pipeline
from app.services.review_pipeline import DiffTooLargeError, PipelineError
from app.services.risk import calculate_risk_score

HEAD_SHA = "c" * 40


@pytest.fixture
def client() -> GitHubClient:
    return MockGitHubClient()


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
        head_ref="feature/coupons",
        base_ref="main",
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def job(db_session: Session, pull_request: PullRequest) -> ReviewJob:
    row = ReviewJob(pull_request_id=pull_request.id, head_sha=HEAD_SHA)
    db_session.add(row)
    db_session.commit()
    return row


class TestFetchDiff:
    def test_parses_the_changed_files(
        self, repository: Repository, pull_request: PullRequest, client: GitHubClient
    ) -> None:
        diff = review_pipeline.fetch_diff(
            repository=repository,
            pull_request=pull_request,
            client=client,
            settings=Settings(_env_file=None),
        )

        assert [f.path for f in diff.files] == ["checkout/coupons.py"]
        assert diff.total_added_lines > 0

    def test_refuses_a_diff_beyond_the_size_limit(
        self, repository: Repository, pull_request: PullRequest, client: GitHubClient
    ) -> None:
        """A huge diff produces a prompt no model reads carefully; saying so is
        more honest than a confidently vague review."""
        huge = _StubDiffClient("".join(_one_file_diff(f"f{n}.py") for n in range(40)))

        with pytest.raises(DiffTooLargeError, match="larger than"):
            review_pipeline.fetch_diff(
                repository=repository,
                pull_request=pull_request,
                client=huge,  # type: ignore[arg-type]
                settings=Settings(_env_file=None, max_diff_bytes=1_000),
            )

    def test_refuses_a_pull_request_touching_too_many_files(
        self, repository: Repository, pull_request: PullRequest
    ) -> None:
        """A sprawling change is reviewed badly by any model, so it is refused
        rather than reviewed carelessly."""
        wide = _StubDiffClient("".join(_one_file_diff(f"f{n}.py") for n in range(5)))

        with pytest.raises(DiffTooLargeError, match="more than the limit"):
            review_pipeline.fetch_diff(
                repository=repository,
                pull_request=pull_request,
                client=wide,  # type: ignore[arg-type]
                settings=Settings(_env_file=None, max_changed_files=2),
            )

    def test_reports_a_repository_with_no_installation(
        self,
        db_session: Session,
        repository: Repository,
        pull_request: PullRequest,
        client: GitHubClient,
    ) -> None:
        """Without an installation there is no token to fetch anything with."""
        repository.installation_id = None
        db_session.flush()

        with pytest.raises(PipelineError, match="installation"):
            review_pipeline.fetch_diff(
                repository=repository,
                pull_request=pull_request,
                client=client,
                settings=Settings(_env_file=None),
            )


class TestStaticAnalysis:
    def test_finds_real_defects_in_the_changed_lines(
        self, repository: Repository, pull_request: PullRequest, client: GitHubClient
    ) -> None:
        settings = Settings(_env_file=None)
        diff = review_pipeline.fetch_diff(
            repository=repository, pull_request=pull_request, client=client, settings=settings
        )

        findings, skipped, _failed = review_pipeline.run_static_analysis(
            repository=repository,
            diff=diff,
            head_sha=HEAD_SHA,
            client=client,
            settings=settings,
        )

        codes = {f.code for f in findings}
        # An unused import, an undefined name and a bare except -- all genuinely
        # wrong, and all introduced by this diff.
        assert {"F401", "F821", "E722"} <= codes
        assert skipped == []

    def test_ignores_problems_the_author_did_not_introduce(
        self, repository: Repository, pull_request: PullRequest, client: GitHubClient
    ) -> None:
        """The mock file imports `json` on line 1, unused and pre-existing. The
        author did not write it and cannot be asked to fix it here."""
        settings = Settings(_env_file=None)
        diff = review_pipeline.fetch_diff(
            repository=repository, pull_request=pull_request, client=client, settings=settings
        )

        findings, _skipped, _failed = review_pipeline.run_static_analysis(
            repository=repository,
            diff=diff,
            head_sha=HEAD_SHA,
            client=client,
            settings=settings,
        )

        assert all(f.line != 1 for f in findings)
        assert not any("`json`" in f.message for f in findings)

    def test_every_finding_lands_on_a_changed_line(
        self, repository: Repository, pull_request: PullRequest, client: GitHubClient
    ) -> None:
        settings = Settings(_env_file=None)
        diff = review_pipeline.fetch_diff(
            repository=repository, pull_request=pull_request, client=client, settings=settings
        )

        findings, _skipped, _failed = review_pipeline.run_static_analysis(
            repository=repository,
            diff=diff,
            head_sha=HEAD_SHA,
            client=client,
            settings=settings,
        )

        changed = diff.files[0].added_lines
        assert all(f.line in changed for f in findings)

    def test_skips_a_file_that_is_too_large_to_analyse(
        self, repository: Repository, pull_request: PullRequest
    ) -> None:
        """Files this big are almost always generated or vendored, and analysing
        them yields findings about code nobody wrote by hand."""
        settings = Settings(_env_file=None, max_file_bytes=1_000)
        generated = _StubDiffClient(_one_file_diff("bundle.py"), content="# generated line\n" * 500)

        diff = review_pipeline.fetch_diff(
            repository=repository,
            pull_request=pull_request,
            client=generated,  # type: ignore[arg-type]
            settings=settings,
        )
        findings, skipped, _failed = review_pipeline.run_static_analysis(
            repository=repository,
            diff=diff,
            head_sha=HEAD_SHA,
            client=generated,  # type: ignore[arg-type]
            settings=settings,
        )

        assert skipped == ["bundle.py"]
        assert findings == []


class TestGatherContext:
    def test_collects_the_diff_and_the_findings(
        self,
        db_session: Session,
        job: ReviewJob,
        pull_request: PullRequest,
        repository: Repository,
        client: GitHubClient,
    ) -> None:
        context = review_pipeline.gather_context(
            db_session,
            job=job,
            pull_request=pull_request,
            repository=repository,
            client=client,
            settings=Settings(_env_file=None),
        )

        assert len(context.diff.files) == 1
        assert context.static_findings
        assert context.skipped_files == []


class TestExecuteReview:
    """The pipeline now runs to completion and stores a review."""

    def test_produces_and_stores_a_review(
        self,
        db_session: Session,
        job: ReviewJob,
        pull_request: PullRequest,
        repository: Repository,
        client: GitHubClient,
    ) -> None:
        outcome = review_pipeline.execute_review(
            db_session,
            job=job,
            pull_request=pull_request,
            repository=repository,
            client=client,
            settings=Settings(_env_file=None),
        )

        assert outcome.summary
        assert outcome.finding_count > 0
        stored = db_session.execute(select(Review)).scalar_one()
        assert stored.review_job_id == job.id
        assert stored.head_sha == job.head_sha

    def test_persists_each_finding(
        self,
        db_session: Session,
        job: ReviewJob,
        pull_request: PullRequest,
        repository: Repository,
        client: GitHubClient,
    ) -> None:
        outcome = review_pipeline.execute_review(
            db_session,
            job=job,
            pull_request=pull_request,
            repository=repository,
            client=client,
            settings=Settings(_env_file=None),
        )

        stored = db_session.execute(select(Finding)).scalars().all()
        assert len(stored) == outcome.finding_count
        assert all(f.file_path == "checkout/coupons.py" for f in stored)

    def test_the_stored_score_is_the_computed_one(
        self,
        db_session: Session,
        job: ReviewJob,
        pull_request: PullRequest,
        repository: Repository,
        client: GitHubClient,
    ) -> None:
        """The score is calculated once and the same number is stored."""
        review_pipeline.execute_review(
            db_session,
            job=job,
            pull_request=pull_request,
            repository=repository,
            client=client,
            settings=Settings(_env_file=None),
        )

        stored = db_session.execute(select(Review)).scalar_one()
        findings = db_session.execute(select(Finding)).scalars().all()
        assert stored.risk_score == calculate_risk_score([f.severity for f in findings])

    def test_records_which_model_produced_it(
        self,
        db_session: Session,
        job: ReviewJob,
        pull_request: PullRequest,
        repository: Repository,
        client: GitHubClient,
    ) -> None:
        """Results stay interpretable after a model upgrade changes the
        character of the findings."""
        review_pipeline.execute_review(
            db_session,
            job=job,
            pull_request=pull_request,
            repository=repository,
            client=client,
            settings=Settings(_env_file=None),
        )

        assert db_session.execute(select(Review)).scalar_one().model_name == "mock-reviewer"

    def test_a_hallucinated_finding_never_reaches_the_database(
        self,
        db_session: Session,
        job: ReviewJob,
        pull_request: PullRequest,
        repository: Repository,
        client: GitHubClient,
    ) -> None:
        """The end-to-end version of the validation tests: a model that invents
        a file must not produce a stored finding."""
        outcome = review_pipeline.execute_review(
            db_session,
            job=job,
            pull_request=pull_request,
            repository=repository,
            client=client,
            provider=_HallucinatingProvider(),
            settings=Settings(_env_file=None),
        )

        assert outcome.finding_count == 0
        assert db_session.execute(select(Finding)).first() is None
        # The summary survives even when every finding is discarded.
        assert db_session.execute(select(Review)).scalar_one().summary


class _HallucinatingProvider:
    """Returns a finding citing a file the pull request never touched."""

    @property
    def model_name(self) -> str:
        return "hallucinating"

    def review(self, *, system_prompt: str, user_prompt: str) -> LLMReviewResponse:
        return LLMReviewResponse(
            review=LLMReview(
                summary="Reviewed.",
                findings=[
                    LLMFinding(
                        category=FindingCategory.SECURITY,
                        severity=FindingSeverity.CRITICAL,
                        file="src/auth/handler.py",
                        line=412,
                        title="Missing authorisation check",
                        description="This endpoint does not verify the caller.",
                        confidence=0.99,
                    )
                ],
            ),
            model_name=self.model_name,
        )


def _one_file_diff(path: str) -> str:
    """A minimal single-file diff, used to build an artificially wide change."""
    return f"""diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1,1 +1,2 @@
 keep
+added
"""


class _StubDiffClient:
    """Serves one canned diff and one canned file body."""

    def __init__(self, diff_text: str, content: str = "x = 1\n") -> None:
        self._diff_text = diff_text
        self._content = content

    def get_pull_request_diff(self, installation_id: int, full_name: str, number: int) -> str:
        return self._diff_text

    def get_file_content(
        self, installation_id: int, full_name: str, path: str, ref: str
    ) -> str | None:
        return self._content
