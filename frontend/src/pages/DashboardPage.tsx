import { ModelText } from '@/components/review/ModelText';
import { RiskScore } from '@/components/review/RiskScore';
import {
  Card,
  CardLink,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  RelativeTime,
  ShortSha,
} from '@/components/ui';
import { useReviews } from '@/features/reviews/hooks';
import { Link } from 'react-router-dom';

export function DashboardPage() {
  const { data: reviews, isPending, isError, error, refetch } = useReviews();

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Recent reviews across every connected repository, worst risk first to eye."
      />

      {isPending && <LoadingState label="Loading reviews…" />}

      {isError && <ErrorState message={error.message} onRetry={() => void refetch()} />}

      {reviews && reviews.length === 0 && (
        <EmptyState
          title="No reviews yet"
          description="Connect a repository and open a pull request. DevPilot reviews each one automatically and the results appear here."
          action={
            <Link to="/repositories" className="text-sm font-medium text-fg underline">
              Go to repositories
            </Link>
          }
        />
      )}

      {reviews && reviews.length > 0 && (
        <div className="space-y-3">
          {reviews.map((review) => (
            <CardLink key={review.id} to={`/reviews/${review.id}`}>
              <div className="flex items-start gap-4">
                <RiskScore score={review.risk_score} size="sm" />

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-sm font-medium text-fg">
                      {review.repository_full_name}
                    </span>
                    <span className="text-sm text-muted">
                      #{review.pull_request_number}
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-sm text-fg">
                    {review.pull_request_title}
                  </p>
                  <ModelText
                    text={review.summary}
                    className="mt-1 line-clamp-2 text-sm text-muted"
                  />
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    <ShortSha sha={review.head_sha} />
                    <RelativeTime iso={review.created_at} />
                    {review.github_review_id === null && (
                      // Says why a review is not on the pull request, rather
                      // than leaving the author to wonder whether it failed.
                      <span className="text-xs text-dim">not posted to GitHub</span>
                    )}
                  </div>
                </div>
              </div>
            </CardLink>
          ))}
        </div>
      )}

      {reviews && reviews.length > 0 && (
        <Card className="mt-6 p-4">
          <p className="text-xs text-muted">
            Risk scores are computed from finding severities in Python, not chosen by the
            model, so the same findings always produce the same score.
          </p>
        </Card>
      )}
    </>
  );
}
