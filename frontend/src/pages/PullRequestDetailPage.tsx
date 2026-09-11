import { ModelText } from '@/components/review/ModelText';
import { RiskScore } from '@/components/review/RiskScore';
import {
  Button,
  Card,
  CardLink,
  ErrorState,
  LoadingState,
  PageHeader,
  RelativeTime,
  ShortSha,
} from '@/components/ui';
import type { JobStatus, ReviewJob } from '@/features/reviews/api';
import { usePullRequest, useRetryReview } from '@/features/reviews/hooks';
import { Link, useParams } from 'react-router-dom';

const JOB_STYLES: Record<JobStatus, string> = {
  queued: 'bg-raised text-fg',
  running: 'bg-severity-low/15 text-severity-low',
  succeeded: 'bg-emerald-400/10 text-emerald-300',
  failed: 'bg-severity-critical/15 text-severity-critical',
  cancelled: 'bg-severity-medium/15 text-severity-medium',
};

export function PullRequestDetailPage() {
  const { pullRequestId } = useParams<{ pullRequestId: string }>();
  const { data, isPending, isError, error, refetch } = usePullRequest(
    pullRequestId ?? '',
  );
  const retry = useRetryReview(pullRequestId ?? '');

  if (isPending) return <LoadingState label="Loading pull request…" />;
  if (isError)
    return <ErrorState message={error.message} onRetry={() => void refetch()} />;

  return (
    <>
      <PageHeader
        title={`#${data.number} ${data.title}`}
        description={`${data.repository_full_name} · ${data.author_login} · ${data.head_ref} → ${data.base_ref}`}
        action={
          <Link
            to={`/repositories/${data.repository_id}`}
            className="text-sm font-medium text-fg underline"
          >
            Repository
          </Link>
        }
      />

      <h2 className="mb-3 text-sm font-semibold text-fg">Reviews</h2>
      {data.reviews.length === 0 ? (
        <Card className="p-6">
          <p className="text-sm text-muted">
            No review yet. If a job below failed, its error says why.
          </p>
        </Card>
      ) : (
        <div className="space-y-3">
          {data.reviews.map((review) => (
            <CardLink key={review.id} to={`/reviews/${review.id}`}>
              <div className="flex items-start gap-4">
                <RiskScore score={review.risk_score} size="sm" />
                <div className="min-w-0 flex-1">
                  <ModelText
                    text={review.summary}
                    className="line-clamp-2 text-sm text-fg"
                  />
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    <ShortSha sha={review.head_sha} />
                    <RelativeTime iso={review.created_at} />
                    <span className="text-xs text-muted">{review.model_name}</span>
                  </div>
                </div>
              </div>
            </CardLink>
          ))}
        </div>
      )}

      <div className="mb-3 mt-8 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-fg">Review attempts</h2>
        {/* Only when the latest attempt failed: that is the one state in which
            the API accepts a retry, and a review can fail for reasons that
            have nothing to do with the code -- the model provider being
            down, say. Without this, the only way to try again is to push. */}
        {latestJobFailed(data.jobs) && (
          <Button onClick={() => retry.mutate()} disabled={retry.isPending}>
            {retry.isPending ? 'Queueing…' : 'Retry review'}
          </Button>
        )}
      </div>
      {retry.isError && <ErrorState message={retry.error.message} />}
      <div className="space-y-2">
        {data.jobs.map((job) => (
          <JobRow key={job.id} job={job} />
        ))}
      </div>
    </>
  );
}

function JobRow({ job }: { job: ReviewJob }) {
  return (
    <Card className="p-3">
      <div className="flex flex-wrap items-center gap-3">
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${JOB_STYLES[job.status]}`}
        >
          {job.status}
        </span>
        <ShortSha sha={job.head_sha} />
        <span className="text-xs text-muted">
          attempt {job.attempts}/{job.max_attempts}
        </span>
        <RelativeTime iso={job.created_at} />
      </div>

      {/* The whole reason failed jobs are shown: "why has this not been
          reviewed?" is answerable without reading worker logs. */}
      {job.error_message && (
        <div className="mt-2 rounded-md bg-severity-critical/10 p-2">
          <p className="text-xs font-medium text-fg">{job.error_type}</p>
          <p className="mt-0.5 text-xs text-muted">{job.error_message}</p>
        </div>
      )}
    </Card>
  );
}

/** Jobs arrive newest first; the retry rule only looks at the latest. */
function latestJobFailed(jobs: ReviewJob[]): boolean {
  return jobs[0]?.status === 'failed';
}
