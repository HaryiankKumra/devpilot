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
import { useRepository, useRepositoryPullRequests } from '@/features/reviews/hooks';
import { useParams } from 'react-router-dom';

const STATE_STYLES: Record<string, string> = {
  open: 'bg-emerald-400/10 text-emerald-300',
  merged: 'bg-violet-100 text-violet-900',
  closed: 'bg-raised text-fg',
};

export function RepositoryDetailPage() {
  const { repositoryId } = useParams<{ repositoryId: string }>();
  const repository = useRepository(repositoryId ?? '');
  const pullRequests = useRepositoryPullRequests(repositoryId ?? '');

  if (repository.isPending) return <LoadingState label="Loading repository…" />;
  if (repository.isError) {
    return (
      <ErrorState
        message={repository.error.message}
        onRetry={() => void repository.refetch()}
      />
    );
  }

  return (
    <>
      <PageHeader
        title={repository.data.full_name}
        description={
          repository.data.indexed_at
            ? 'Indexed for semantic search, so reviews can retrieve related code.'
            : 'Not indexed yet. Reviews will run without wider repository context.'
        }
      />

      {pullRequests.isPending && <LoadingState label="Loading pull requests…" />}
      {pullRequests.isError && <ErrorState message={pullRequests.error.message} />}

      {pullRequests.data && pullRequests.data.length === 0 && (
        <EmptyState
          title="No pull requests yet"
          description="Pull requests appear here once GitHub sends a webhook for them. Nothing is imported retroactively."
        />
      )}

      {pullRequests.data && pullRequests.data.length > 0 && (
        <div className="space-y-3">
          {pullRequests.data.map((pullRequest) => (
            <CardLink key={pullRequest.id} to={`/pull-requests/${pullRequest.id}`}>
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-sm text-muted">#{pullRequest.number}</span>
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg">
                  {pullRequest.title}
                </span>
                <span
                  className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${
                    STATE_STYLES[pullRequest.state] ?? STATE_STYLES.closed
                  }`}
                >
                  {pullRequest.state}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted">
                <span>{pullRequest.author_login}</span>
                <ShortSha sha={pullRequest.head_sha} />
                <span>
                  {pullRequest.head_ref} → {pullRequest.base_ref}
                </span>
                <RelativeTime iso={pullRequest.created_at} />
              </div>
            </CardLink>
          ))}
        </div>
      )}

      <Card className="mt-6 p-4">
        <p className="text-xs text-muted">
          Default branch {repository.data.default_branch} ·{' '}
          {repository.data.is_private ? 'private' : 'public'} · GitHub id{' '}
          {repository.data.github_repo_id}
        </p>
      </Card>
    </>
  );
}
