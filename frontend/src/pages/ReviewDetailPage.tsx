import { ModelText } from '@/components/review/ModelText';
import { CategoryBadge, RiskScore, SeverityBadge } from '@/components/review/RiskScore';
import {
  Button,
  Card,
  ErrorState,
  LoadingState,
  PageHeader,
  RelativeTime,
  ShortSha,
} from '@/components/ui';
import type { Finding } from '@/features/reviews/api';
import { usePublishReview, useReview } from '@/features/reviews/hooks';
import { Link, useParams } from 'react-router-dom';

export function ReviewDetailPage() {
  const { reviewId } = useParams<{ reviewId: string }>();
  const { data: review, isPending, isError, error, refetch } = useReview(reviewId ?? '');
  const publish = usePublishReview(reviewId ?? '');

  if (isPending) return <LoadingState label="Loading review…" />;
  if (isError)
    return <ErrorState message={error.message} onRetry={() => void refetch()} />;

  const posted = review.findings.filter((f) => f.is_posted).length;

  return (
    <>
      <PageHeader
        title="Review"
        description={`Reviewed by ${review.model_name}`}
        action={
          <Link
            to={`/pull-requests/${review.pull_request_id}`}
            className="text-sm font-medium text-fg underline"
          >
            View pull request
          </Link>
        }
      />

      <Card className="mb-6 p-5">
        <div className="flex flex-wrap items-start gap-6">
          <RiskScore score={review.risk_score} />
          <div className="min-w-0 flex-1">
            <ModelText text={review.summary} className="text-sm text-fg" />
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <ShortSha sha={review.head_sha} />
              <RelativeTime iso={review.created_at} />
              {review.prompt_tokens !== null && (
                <span className="text-xs text-muted">
                  {review.prompt_tokens.toLocaleString()} in /{' '}
                  {review.completion_tokens?.toLocaleString() ?? '—'} out tokens
                </span>
              )}
            </div>
          </div>
        </div>
      </Card>

      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-fg">
          {review.findings.length} finding{review.findings.length === 1 ? '' : 's'}
        </h2>
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-xs text-muted">
            {posted} posted to GitHub · {review.findings.length - posted} kept here only
          </p>
          {/* Only while nothing has reached GitHub yet. The post is the last
              step of the pipeline and can fail on its own -- the App lacking
              write access, say -- without failing the review; this is how a
              stranded review gets sent once the cause is fixed. */}
          {review.github_review_id === null && review.findings.length > 0 && (
            <Button
              variant="secondary"
              onClick={() => publish.mutate()}
              disabled={publish.isPending}
            >
              {publish.isPending ? 'Posting…' : 'Post to GitHub'}
            </Button>
          )}
        </div>
      </div>
      {publish.isError && <ErrorState message={publish.error.message} />}
      {publish.isSuccess && !publish.data.posted && (
        <Card className="mb-4 p-3">
          <p className="text-sm text-fg">Nothing posted: {publish.data.skipped_reason}</p>
        </Card>
      )}

      {review.findings.length === 0 ? (
        <Card className="p-8 text-center">
          <p className="text-sm font-medium text-fg">Nothing found</p>
          <p className="mt-1 text-sm text-muted">
            A clean result is a real answer, not a failure — the reviewer is told to
            report nothing rather than invent nitpicks.
          </p>
        </Card>
      ) : (
        <div className="space-y-3">
          {review.findings.map((finding) => (
            <FindingCard key={finding.id} finding={finding} />
          ))}
        </div>
      )}
    </>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge severity={finding.severity} />
        <CategoryBadge category={finding.category} />
        <code className="font-mono text-xs text-muted">
          {finding.file_path}
          {finding.line !== null && `:${finding.line}`}
        </code>
        <span className="ml-auto text-xs text-muted">
          {Math.round(finding.confidence * 100)}% confident
        </span>
      </div>

      <ModelText text={finding.title} className="mt-2 text-sm font-medium text-fg" />
      <ModelText text={finding.description} className="mt-1 text-sm text-fg" />

      {finding.suggestion && (
        <div className="mt-3 rounded-md border border-line bg-surface p-3">
          <p className="eyebrow">Suggestion</p>
          <ModelText text={finding.suggestion} className="mt-2 text-sm text-fg" />
        </div>
      )}

      {!finding.is_posted && (
        // Explains the difference between what is here and what reached the
        // pull request, so the gap does not read as a bug.
        <p className="mt-3 text-xs text-muted">
          {finding.line === null
            ? 'Concerns the file as a whole, so there is no line to comment on.'
            : 'Below the confidence threshold for posting, so it stays here.'}
        </p>
      )}
    </Card>
  );
}
