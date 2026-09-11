/** GitHub account linking, from the browser's side. */

import { apiFetch } from '@/lib/apiClient';

export interface GitHubLinkStatus {
  linked: boolean;
  github_login: string | null;
  install_url: string | null;
}

interface GitHubAuthorizeStart {
  authorize_url: string;
}

export function fetchGitHubStatus(): Promise<GitHubLinkStatus> {
  return apiFetch<GitHubLinkStatus>('/api/v1/github/status');
}

export function unlinkGitHub(): Promise<unknown> {
  return apiFetch('/api/v1/github/link', { method: 'DELETE' });
}

/**
 * Start linking a GitHub account.
 *
 * Two hops rather than one link: `/authorize` needs the Bearer token to know
 * *which* user is linking, and a plain `<a href>` navigation cannot send one.
 * So the URL is fetched with the token attached, and only then does the browser
 * leave for GitHub. The signed `state` inside that URL is what ties the eventual
 * callback back to this user, so nothing is lost by the extra step.
 *
 * In mock mode the URL points back at DevPilot's own callback, so the whole
 * flow completes locally with no GitHub account involved.
 */
export async function startGitHubLink(): Promise<void> {
  const { authorize_url } = await apiFetch<GitHubAuthorizeStart>(
    '/api/v1/github/authorize',
  );
  window.location.assign(authorize_url);
}
