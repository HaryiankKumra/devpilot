/** GitHub account linking, from the browser's side. */

import { apiFetch } from '@/lib/apiClient';

export interface GitHubLinkStatus {
  linked: boolean;
  github_login: string | null;
  install_url: string | null;
}

export function fetchGitHubStatus(): Promise<GitHubLinkStatus> {
  return apiFetch<GitHubLinkStatus>('/api/v1/github/status');
}

export function unlinkGitHub(): Promise<unknown> {
  return apiFetch('/api/v1/github/link', { method: 'DELETE' });
}

/*
 * Note: there is deliberately no helper for starting the OAuth flow from the
 * browser. `/api/v1/github/authorize` requires a Bearer token, and a plain
 * navigation cannot send one — so linking has to be started from a context
 * that can carry the header, or the endpoint has to accept a short-lived
 * one-time link. That is unresolved, and pretending otherwise with a URL that
 * would 401 would be worse than saying so.
 */
