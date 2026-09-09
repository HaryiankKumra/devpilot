/** Shared React Query client and its caching policy. */

import { ApiError } from '@/lib/apiClient';
import { QueryClient } from '@tanstack/react-query';

const MAX_RETRIES = 2;

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Review data changes only when a webhook fires, so briefly serving
      // cached data avoids refetching on every navigation.
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Retrying a 404 or a 401 just repeats the same answer more slowly.
        if (error instanceof ApiError && !error.isTransient) {
          return false;
        }
        return failureCount < MAX_RETRIES;
      },
    },
    mutations: {
      // Mutations are not idempotent; a blind retry could double-submit.
      retry: false,
    },
  },
});
