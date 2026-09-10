/** API bindings and types for reviews, repositories and pull requests. */

import { apiFetch } from '@/lib/apiClient';

export type Severity = 'critical' | 'high' | 'medium' | 'low';
export type Category = 'bug' | 'security' | 'performance' | 'quality';
export type PullRequestState = 'open' | 'closed' | 'merged';
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

export interface Finding {
  id: string;
  category: Category;
  severity: Severity;
  file_path: string;
  line: number | null;
  title: string;
  description: string;
  suggestion: string | null;
  confidence: number;
  is_posted: boolean;
}

export interface ReviewSummary {
  id: string;
  pull_request_id: string;
  summary: string;
  risk_score: number;
  head_sha: string;
  model_name: string;
  github_review_id: number | null;
  created_at: string;
}

export interface ReviewListItem extends ReviewSummary {
  repository_full_name: string;
  pull_request_number: number;
  pull_request_title: string;
}

export interface ReviewDetail extends ReviewSummary {
  findings: Finding[];
  prompt_tokens: number | null;
  completion_tokens: number | null;
}

export interface ReviewJob {
  id: string;
  status: JobStatus;
  head_sha: string;
  attempts: number;
  max_attempts: number;
  error_type: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface PullRequestDetail {
  id: string;
  repository_id: string;
  repository_full_name: string;
  number: number;
  title: string;
  author_login: string;
  state: PullRequestState;
  head_sha: string;
  head_ref: string;
  base_ref: string;
  created_at: string;
  jobs: ReviewJob[];
  reviews: ReviewSummary[];
}

export interface Repository {
  id: string;
  github_repo_id: number;
  full_name: string;
  default_branch: string;
  is_private: boolean;
  is_active: boolean;
  installation_id: number | null;
  indexed_at: string | null;
  created_at: string;
}

export interface PullRequestListItem {
  id: string;
  repository_id: string;
  number: number;
  title: string;
  author_login: string;
  state: PullRequestState;
  head_sha: string;
  head_ref: string;
  base_ref: string;
  created_at: string;
}

export interface Installation {
  id: number;
  account_login: string | null;
  repository_selection: string | null;
}

export interface SyncResult {
  created: number;
  updated: number;
  deactivated: number;
}

export interface IndexResult {
  repository_id: string;
  queued: boolean;
  celery_task_id: string | null;
  detail: string;
}

export function fetchReviews(limit = 20): Promise<ReviewListItem[]> {
  return apiFetch<ReviewListItem[]>(`/api/v1/reviews?limit=${limit}`);
}

export function fetchReview(reviewId: string): Promise<ReviewDetail> {
  return apiFetch<ReviewDetail>(`/api/v1/reviews/${reviewId}`);
}

export function fetchPullRequest(pullRequestId: string): Promise<PullRequestDetail> {
  return apiFetch<PullRequestDetail>(`/api/v1/pull-requests/${pullRequestId}`);
}

export function fetchRepositories(): Promise<Repository[]> {
  return apiFetch<Repository[]>('/api/v1/repositories');
}

export function fetchRepository(repositoryId: string): Promise<Repository> {
  return apiFetch<Repository>(`/api/v1/repositories/${repositoryId}`);
}

export function fetchRepositoryPullRequests(
  repositoryId: string,
): Promise<PullRequestListItem[]> {
  return apiFetch<PullRequestListItem[]>(
    `/api/v1/repositories/${repositoryId}/pull-requests`,
  );
}

export function fetchInstallations(): Promise<Installation[]> {
  return apiFetch<Installation[]>('/api/v1/repositories/installations');
}

export function syncRepositories(installationId: number): Promise<SyncResult> {
  return apiFetch<SyncResult>('/api/v1/repositories/sync', {
    method: 'POST',
    body: JSON.stringify({ installation_id: installationId }),
  });
}

export function indexRepository(repositoryId: string): Promise<IndexResult> {
  return apiFetch<IndexResult>(`/api/v1/repositories/${repositoryId}/index`, {
    method: 'POST',
  });
}
