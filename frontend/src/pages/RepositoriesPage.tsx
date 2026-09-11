import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  RelativeTime,
} from '@/components/ui';
import {
  useIndexRepository,
  useInstallations,
  useRepositories,
  useSyncRepositories,
} from '@/features/reviews/hooks';
import { Link } from 'react-router-dom';

export function RepositoriesPage() {
  const { data: repositories, isPending, isError, error, refetch } = useRepositories();
  const { data: installations } = useInstallations();
  const sync = useSyncRepositories();
  const index = useIndexRepository();

  const installationId = installations?.[0]?.id;

  return (
    <>
      <PageHeader
        title="Repositories"
        description="Repositories DevPilot watches. Syncing reconciles this list with what the GitHub App can see."
        action={
          installationId !== undefined && (
            <Button onClick={() => sync.mutate(installationId)} disabled={sync.isPending}>
              {sync.isPending ? 'Syncing…' : 'Sync from GitHub'}
            </Button>
          )
        }
      />

      {sync.isError && <ErrorState message={sync.error.message} />}
      {sync.isSuccess && (
        <Card className="mb-4 p-3">
          <p className="text-sm text-slate-700">
            {sync.data.created} added · {sync.data.updated} updated ·{' '}
            {sync.data.deactivated} deactivated
          </p>
        </Card>
      )}

      {isPending && <LoadingState label="Loading repositories…" />}
      {isError && <ErrorState message={error.message} onRetry={() => void refetch()} />}

      {repositories && repositories.length === 0 && installationId === undefined && (
        /* An installation can only be synced by the account whose linked GitHub
           identity owns it, so until the account is linked there is nothing to
           offer -- and saying so beats a Sync button that does nothing. */
        <EmptyState
          title="Connect GitHub first"
          description="DevPilot needs to know which GitHub account is yours before it can tell which App installation to sync from."
          action={
            <Link
              to="/settings"
              className="inline-flex items-center rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700"
            >
              Connect GitHub
            </Link>
          }
        />
      )}

      {repositories && repositories.length === 0 && installationId !== undefined && (
        <EmptyState
          title="No repositories connected"
          description="Install the DevPilot GitHub App on a repository, then press Sync. Running in mock mode? Sync anyway — a demo repository is provided."
          action={
            <Button onClick={() => sync.mutate(installationId)}>Sync from GitHub</Button>
          }
        />
      )}

      {repositories && repositories.length > 0 && (
        <div className="space-y-3">
          {repositories.map((repository) => (
            <Card key={repository.id} className="p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <Link
                    to={`/repositories/${repository.id}`}
                    className="text-sm font-medium text-slate-900 hover:underline"
                  >
                    {repository.full_name}
                  </Link>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                    <span>{repository.is_private ? 'Private' : 'Public'}</span>
                    <span>·</span>
                    <span>{repository.default_branch}</span>
                    {!repository.is_active && (
                      <>
                        <span>·</span>
                        {/* Deactivated rather than deleted, so history survives
                            an uninstall. */}
                        <span className="text-amber-700">app uninstalled</span>
                      </>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  {repository.indexed_at ? (
                    <span className="text-xs text-slate-500">
                      indexed <RelativeTime iso={repository.indexed_at} />
                    </span>
                  ) : (
                    <span className="text-xs text-slate-400">not indexed</span>
                  )}
                  <Button
                    variant="secondary"
                    onClick={() => index.mutate(repository.id)}
                    disabled={index.isPending}
                  >
                    {repository.indexed_at ? 'Re-index' : 'Index'}
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Card className="mt-6 p-4">
        <p className="text-xs text-slate-500">
          Indexing embeds the repository so reviews can retrieve related code. It runs on
          a worker and takes a moment; re-indexing only embeds what changed.
        </p>
      </Card>
    </>
  );
}
