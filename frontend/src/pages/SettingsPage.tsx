import { Button, Card, ErrorState, LoadingState, PageHeader } from '@/components/ui';
import {
  fetchGitHubStatus,
  startGitHubLink,
  unlinkGitHub,
} from '@/features/auth/githubApi';
import { useAuth } from '@/features/auth/useAuth';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

export function SettingsPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();

  const status = useQuery({ queryKey: ['github', 'status'], queryFn: fetchGitHubStatus });
  const unlink = useMutation({
    mutationFn: unlinkGitHub,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['github', 'status'] }),
  });
  // Navigates away on success, so there is nothing to invalidate here; the
  // callback lands the browser back on this page with fresh status.
  const link = useMutation({ mutationFn: startGitHubLink });

  return (
    <>
      <PageHeader
        title="Settings"
        description="Your account and its GitHub connection."
      />

      <Card className="mb-6 p-5">
        <h2 className="text-sm font-semibold text-slate-900">Account</h2>
        <dl className="mt-3 space-y-2 text-sm">
          <div className="flex gap-3">
            <dt className="w-32 text-slate-500">Email</dt>
            <dd className="text-slate-900">{user?.email ?? '—'}</dd>
          </div>
          <div className="flex gap-3">
            <dt className="w-32 text-slate-500">Name</dt>
            <dd className="text-slate-900">{user?.full_name ?? '—'}</dd>
          </div>
        </dl>
      </Card>

      <Card className="p-5">
        <h2 className="text-sm font-semibold text-slate-900">GitHub</h2>

        {status.isPending && <LoadingState label="Checking connection…" />}
        {status.isError && <ErrorState message={status.error.message} />}

        {status.data && (
          <div className="mt-3">
            {status.data.linked ? (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-slate-700">
                  Connected as{' '}
                  <span className="font-medium text-slate-900">
                    {status.data.github_login}
                  </span>
                </p>
                <Button
                  variant="secondary"
                  onClick={() => unlink.mutate()}
                  disabled={unlink.isPending}
                >
                  Disconnect
                </Button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-slate-700">
                  No GitHub account connected. Linking lets DevPilot tell which
                  installation belongs to you.
                </p>
                <Button onClick={() => link.mutate()} disabled={link.isPending}>
                  {link.isPending ? 'Redirecting…' : 'Connect GitHub'}
                </Button>
              </div>
            )}
            {link.isError && <ErrorState message={link.error.message} />}

            {status.data.install_url && (
              <p className="mt-3 text-sm">
                <a
                  href={status.data.install_url}
                  className="font-medium text-slate-900 underline"
                  target="_blank"
                  rel="noreferrer"
                >
                  Install the GitHub App on a repository
                </a>
              </p>
            )}

            {!status.data.install_url && (
              /* No app slug configured, so the link would lead nowhere. */
              <p className="mt-3 text-xs text-slate-500">
                Running in mock mode — no GitHub App is configured, and none is needed.
                See docs/github-app-setup.md to connect a real one.
              </p>
            )}
          </div>
        )}
      </Card>
    </>
  );
}
