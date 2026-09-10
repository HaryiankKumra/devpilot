"""The review pipeline: the work a review job actually performs.

The pipeline is a sequence of stages, and it is being filled in one milestone at
a time. What runs today:

* **Fetch the diff** (Milestone 6) -- ask GitHub for the unified diff and parse
  it into per-file change sets.
* **Static analysis** (Milestone 6) -- run parser-based analysers over the
  changed files, keeping only findings on lines the pull request touched.
* **LLM review** (Milestone 7) -- ask the model, then discard every finding
  the diff does not support.
* **Score and persist** (Milestone 7) -- compute the risk score in Python from
  the surviving findings, and store the review.
* **Retrieval** (Milestone 8) -- find related code in the repository index by
  vector similarity, so the model can see what the diff calls into.
* **Post to GitHub** (Milestone 9) -- not built. Findings are stored and shown
  on the dashboard, but nothing is written back to the pull request.

`gather_context` is deliberately separate from `execute_review` so the finished
stages can be called, tested and inspected without the unfinished ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models.finding import Finding
from app.db.models.pull_request import PullRequest
from app.db.models.repository import Repository
from app.db.models.review import Review
from app.db.models.review_job import ReviewJob
from app.integrations.embeddings.factory import build_embedding_provider
from app.integrations.embeddings.provider import EmbeddingError, EmbeddingProvider
from app.integrations.github.client import GitHubClient
from app.integrations.llm.factory import build_llm_provider
from app.integrations.llm.provider import LLMProvider
from app.services import llm_review as llm_review_service
from app.services.diff import FileDiff, ParsedDiff, parse_unified_diff
from app.services.retrieval import RetrievedChunk, retrieve_context
from app.services.risk import calculate_risk_score
from app.services.static_analysis import StaticFinding, analyze_file_detailed

logger = get_logger(__name__)


class PipelineError(Exception):
    """A review could not be produced."""


class PipelineNotImplementedError(PipelineError):
    """The remaining review stages have not been built yet.

    Treated as a *permanent* failure by the worker: retrying cannot make an
    unwritten stage appear, and burning three attempts on it would only obscure
    the real reason in the job's error record.
    """


class DiffTooLargeError(PipelineError):
    """The pull request is too big to review usefully.

    Permanent rather than transient: the diff will not shrink on a retry.
    """


@dataclass(frozen=True)
class ReviewOutcome:
    """What a completed pipeline produced.

    Returned rather than written directly so the pipeline stays a pure function
    of its inputs, and the caller owns the transaction.
    """

    summary: str
    risk_score: int
    model_name: str
    finding_count: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class ReviewContext:
    """Everything gathered before the LLM is asked anything.

    This is what Milestone 7 will turn into a prompt.
    """

    diff: ParsedDiff
    static_findings: list[StaticFinding] = field(default_factory=list)
    # Files whose contents could not be read at the head commit -- deleted later
    # in the branch, or too large. Recorded so the LLM is not told about files
    # nobody analysed.
    skipped_files: list[str] = field(default_factory=list)
    # Analysers that failed to run. Kept so a degraded review can say so rather
    # than presenting fewer findings as if the code were cleaner.
    failed_analyzers: list[str] = field(default_factory=list)

    # Repository code retrieved by similarity, so the model can see what the
    # diff calls into. Empty when the repository has not been indexed.
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)

    @property
    def analysed_file_count(self) -> int:
        return len(self.diff.reviewable_files) - len(self.skipped_files)

    @property
    def static_analysis_complete(self) -> bool:
        return not self.failed_analyzers


def fetch_diff(
    *,
    repository: Repository,
    pull_request: PullRequest,
    client: GitHubClient,
    settings: Settings,
) -> ParsedDiff:
    """Retrieve and parse the pull request diff.

    Raises `DiffTooLargeError` when the change is beyond what is worth
    reviewing. The limit is not arbitrary frugality: a 5,000-line diff produces
    a prompt no model reads carefully, and the resulting review is confidently
    vague. Saying "too large to review" is more honest than that.
    """
    if repository.installation_id is None:
        raise PipelineError(
            f"{repository.full_name} has no GitHub App installation, so its diff cannot be fetched."
        )

    diff_text = client.get_pull_request_diff(
        repository.installation_id, repository.full_name, pull_request.number
    )

    if len(diff_text.encode("utf-8")) > settings.max_diff_bytes:
        raise DiffTooLargeError(
            f"The diff is larger than {settings.max_diff_bytes} bytes and will not be reviewed."
        )

    parsed = parse_unified_diff(diff_text)

    if len(parsed.files) > settings.max_changed_files:
        raise DiffTooLargeError(
            f"The pull request changes {len(parsed.files)} files, more than the "
            f"limit of {settings.max_changed_files}."
        )

    logger.info(
        "pipeline.diff_fetched",
        repository=repository.full_name,
        pull_request=pull_request.number,
        files=len(parsed.files),
        added_lines=parsed.total_added_lines,
    )
    return parsed


def run_static_analysis(
    *,
    repository: Repository,
    diff: ParsedDiff,
    head_sha: str,
    client: GitHubClient,
    settings: Settings,
) -> tuple[list[StaticFinding], list[str], list[str]]:
    """Analyse every changed file, returning findings and the files skipped.

    Each file is fetched at the head commit rather than reconstructed from the
    diff: an analyser needs the whole file to resolve imports and scope, and a
    diff only carries the changed hunks.
    """
    findings: list[StaticFinding] = []
    skipped: list[str] = []
    failed: set[str] = set()

    for file_diff in diff.reviewable_files:
        content = _read_file(
            repository=repository,
            file_diff=file_diff,
            head_sha=head_sha,
            client=client,
            settings=settings,
        )
        if content is None:
            skipped.append(file_diff.path)
            continue

        result = analyze_file_detailed(file_diff, content)
        findings.extend(result.findings)
        failed.update(result.failed_analyzers)

    logger.info(
        "pipeline.static_analysis_completed",
        repository=repository.full_name,
        findings=len(findings),
        skipped=len(skipped),
        failed_analyzers=sorted(failed),
    )
    return findings, skipped, sorted(failed)


def _read_file(
    *,
    repository: Repository,
    file_diff: FileDiff,
    head_sha: str,
    client: GitHubClient,
    settings: Settings,
) -> str | None:
    """Fetch one file at the head commit, or `None` if it cannot be analysed."""
    if repository.installation_id is None:
        return None

    content = client.get_file_content(
        repository.installation_id, repository.full_name, file_diff.path, head_sha
    )
    if content is None:
        return None

    if len(content.encode("utf-8")) > settings.max_file_bytes:
        # Usually generated or vendored. Analysing it produces a wall of
        # findings about code nobody wrote by hand.
        logger.info("pipeline.file_too_large", path=file_diff.path)
        return None

    return content


def gather_context(
    session: Session,
    *,
    job: ReviewJob,
    pull_request: PullRequest,
    repository: Repository,
    client: GitHubClient,
    embedder: EmbeddingProvider | None = None,
    settings: Settings | None = None,
) -> ReviewContext:
    """Run every stage that exists today and return what they produced."""
    settings = settings or get_settings()

    diff = fetch_diff(
        repository=repository,
        pull_request=pull_request,
        client=client,
        settings=settings,
    )
    static_findings, skipped, failed_analyzers = run_static_analysis(
        repository=repository,
        diff=diff,
        # The job's head SHA, not the pull request's: the branch may have moved
        # on since this job was queued, and the review must describe the commit
        # it was created for.
        head_sha=job.head_sha,
        client=client,
        settings=settings,
    )

    retrieved = _retrieve(
        session,
        repository=repository,
        diff=diff,
        embedder=embedder,
        settings=settings,
    )

    return ReviewContext(
        diff=diff,
        static_findings=static_findings,
        skipped_files=skipped,
        failed_analyzers=failed_analyzers,
        retrieved_chunks=retrieved,
    )


def _retrieve(
    session: Session,
    *,
    repository: Repository,
    diff: ParsedDiff,
    embedder: EmbeddingProvider | None,
    settings: Settings,
) -> list[RetrievedChunk]:
    """Fetch related repository code, or return nothing if that is not possible.

    Retrieval is an enhancement, not a prerequisite. A repository that has never
    been indexed, or an embedding provider that is down, should produce a review
    without repository context rather than no review at all.
    """
    if repository.indexed_at is None:
        logger.info("pipeline.retrieval_skipped", reason="repository not indexed")
        return []

    try:
        return retrieve_context(
            session,
            repository_id=repository.id,
            diff=diff,
            embedder=embedder or build_embedding_provider(settings),
            settings=settings,
        )
    except EmbeddingError as exc:
        logger.warning("pipeline.retrieval_failed", error=str(exc), error_type=type(exc).__name__)
        return []


def persist_review(
    session: Session,
    *,
    job: ReviewJob,
    pull_request: PullRequest,
    validated: llm_review_service.ValidatedReview,
    risk_score: int,
) -> Review:
    """Write the review and its findings.

    The risk score is passed in rather than recomputed here: it is calculated
    once, from the validated findings, and the same number is stored, displayed
    and posted.
    """
    review = Review(
        review_job_id=job.id,
        pull_request_id=pull_request.id,
        summary=validated.summary,
        risk_score=risk_score,
        head_sha=job.head_sha,
        model_name=validated.model_name,
        prompt_tokens=validated.prompt_tokens,
        completion_tokens=validated.completion_tokens,
    )
    session.add(review)
    session.flush()

    for finding in validated.findings:
        session.add(
            Finding(
                review_id=review.id,
                category=finding.category,
                severity=finding.severity,
                file_path=finding.file,
                line=finding.line,
                title=finding.title,
                description=finding.description,
                suggestion=finding.suggestion,
                confidence=finding.confidence,
            )
        )
    session.flush()
    return review


def execute_review(
    session: Session,
    *,
    job: ReviewJob,
    pull_request: PullRequest,
    repository: Repository,
    client: GitHubClient,
    provider: LLMProvider | None = None,
    embedder: EmbeddingProvider | None = None,
    settings: Settings | None = None,
) -> ReviewOutcome:
    """Produce and store a review for one pull request at one commit.

    The stages run in order: fetch and parse the diff, analyse the changed
    files, ask the model, discard every finding the diff does not support,
    compute the risk score in Python, and persist the result.

    Posting the surviving findings to GitHub is Milestone 9; they are stored and
    visible on the dashboard until then.
    """
    settings = settings or get_settings()
    provider = provider or build_llm_provider(settings)

    context = gather_context(
        session,
        job=job,
        pull_request=pull_request,
        repository=repository,
        client=client,
        embedder=embedder,
        settings=settings,
    )

    validated = llm_review_service.request_review(
        context,
        provider=provider,
        repository_full_name=repository.full_name,
        pull_request_title=pull_request.title,
        pull_request_number=pull_request.number,
        max_retries=settings.llm_max_validation_retries,
    )

    # Computed here, from validated findings only. A finding the diff did not
    # support has already been discarded and cannot influence the score.
    risk_score = calculate_risk_score([f.severity for f in validated.findings])

    review = persist_review(
        session,
        job=job,
        pull_request=pull_request,
        validated=validated,
        risk_score=risk_score,
    )

    logger.info(
        "pipeline.review_completed",
        review_job_id=str(job.id),
        review_id=str(review.id),
        repository=repository.full_name,
        pull_request=pull_request.number,
        risk_score=risk_score,
        findings=len(validated.findings),
        rejected_findings=validated.rejection_count,
        model=validated.model_name,
        static_analysis_complete=context.static_analysis_complete,
        retrieved_chunks=len(context.retrieved_chunks),
    )

    return ReviewOutcome(
        summary=validated.summary,
        risk_score=risk_score,
        model_name=validated.model_name,
        finding_count=len(validated.findings),
        prompt_tokens=validated.prompt_tokens,
        completion_tokens=validated.completion_tokens,
    )
