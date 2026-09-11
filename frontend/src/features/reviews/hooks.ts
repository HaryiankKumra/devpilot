/**
 * React Query hooks for review data.
 *
 * Query keys are hierarchical (`['reviews']` → `['reviews', id]`) so that
 * invalidating a parent key refreshes every list containing that entity,
 * without having to know which lists exist.
 */

import {
  fetchInstallations,
  fetchPullRequest,
  fetchRepositories,
  fetchRepository,
  fetchRepositoryPullRequests,
  fetchReview,
  fetchReviews,
  indexRepository,
  publishReview,
  retryReview,
  syncRepositories,
} from '@/features/reviews/api';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

export const queryKeys = {
  reviews: ['reviews'] as const,
  review: (id: string) => ['reviews', id] as const,
  repositories: ['repositories'] as const,
  repository: (id: string) => ['repositories', id] as const,
  repositoryPullRequests: (id: string) => ['repositories', id, 'pull-requests'] as const,
  pullRequest: (id: string) => ['pull-requests', id] as const,
  installations: ['installations'] as const,
};

export function useReviews(limit = 20) {
  return useQuery({
    queryKey: [...queryKeys.reviews, limit],
    queryFn: () => fetchReviews(limit),
  });
}

export function useReview(reviewId: string) {
  return useQuery({
    queryKey: queryKeys.review(reviewId),
    queryFn: () => fetchReview(reviewId),
  });
}

export function useRepositories() {
  return useQuery({ queryKey: queryKeys.repositories, queryFn: fetchRepositories });
}

export function useRepository(repositoryId: string) {
  return useQuery({
    queryKey: queryKeys.repository(repositoryId),
    queryFn: () => fetchRepository(repositoryId),
  });
}

export function useRepositoryPullRequests(repositoryId: string) {
  return useQuery({
    queryKey: queryKeys.repositoryPullRequests(repositoryId),
    queryFn: () => fetchRepositoryPullRequests(repositoryId),
  });
}

export function usePullRequest(pullRequestId: string) {
  return useQuery({
    queryKey: queryKeys.pullRequest(pullRequestId),
    queryFn: () => fetchPullRequest(pullRequestId),
    // A queued or running job changes without the user doing anything, so this
    // page polls. Everything else is only stale when the user acts.
    refetchInterval: 10_000,
  });
}

export function useInstallations() {
  return useQuery({ queryKey: queryKeys.installations, queryFn: fetchInstallations });
}

export function useSyncRepositories() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: syncRepositories,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.repositories }),
  });
}

export function useIndexRepository() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: indexRepository,
    // Indexing runs on a worker, so the row is not updated by the time this
    // resolves. Invalidating still refreshes the list on the next poll or
    // navigation, which is when the user would notice.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.repositories }),
  });
}

export function useRetryReview(pullRequestId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => retryReview(pullRequestId),
    // The new attempt appears in the job list immediately as `queued`; its
    // outcome lands on the next refetch, once the worker has run.
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.pullRequest(pullRequestId) }),
  });
}

export function usePublishReview(reviewId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => publishReview(reviewId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.review(reviewId) }),
  });
}
