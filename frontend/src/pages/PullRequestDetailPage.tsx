import { RiskScore } from '@/components/review/RiskScore';
import {
  Card,
  CardLink,
  ErrorState,
  LoadingState,
  PageHeader,
  RelativeTime,
  ShortSha,
} from '@/components/ui';
import type { JobStatus, ReviewJob } from '@/features/reviews/api';
import { usePullRequest } from '@/features/reviews/hooks';
import { Link, useParams } from 'react-router-dom';

const JOB_STYLES: Record<JobStatus, string> = {
  queued: 'bg-slate-100 text-slate-700',
  running: 'bg-sky-100 text-sky-900',
  succeeded: 'bg-emerald-100 text-emerald-900',
  failed: 'bg-red-100 text-red-900',
  cancelled: 'bg-amber-100 text-amber-900',
};

export function PullRequestDetailPage() {
  const { pullRequestId } = useParams<{ pullRequestId: string }>();
  const { data, isPending, isError, error, refetch } = usePullRequest(
    pullRequestId ?? '',
  );

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
            className="text-sm font-medium text-slate-900 underline"
          >
            Repository
          </Link>
        }
      />

      <h2 className="mb-3 text-sm font-semibold text-slate-900">Reviews</h2>
      {data.reviews.length === 0 ? (
        <Card className="p-6">
          <p className="text-sm text-slate-600">
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
                  <p className="line-clamp-2 text-sm text-slate-700">{review.summary}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    <ShortSha sha={review.head_sha} />
                    <RelativeTime iso={review.created_at} />
                    <span className="text-xs text-slate-500">{review.model_name}</span>
                  </div>
                </div>
              </div>
            </CardLink>
          ))}
        </div>
      )}

      <h2 className="mb-3 mt-8 text-sm font-semibold text-slate-900">Review attempts</h2>
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
        <span className="text-xs text-slate-500">
          attempt {job.attempts}/{job.max_attempts}
        </span>
        <RelativeTime iso={job.created_at} />
      </div>

      {/* The whole reason failed jobs are shown: "why has this not been
          reviewed?" is answerable without reading worker logs. */}
      {job.error_message && (
        <div className="mt-2 rounded-md bg-red-50 p-2">
          <p className="text-xs font-medium text-red-900">{job.error_type}</p>
          <p className="mt-0.5 text-xs text-red-800">{job.error_message}</p>
        </div>
      )}
    </Card>
  );
}
