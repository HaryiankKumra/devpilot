/**
 * Reports whether the browser can currently reach the DevPilot API.
 *
 * This exists mainly as a development aid: during local setup the most common
 * failure is a misconfigured `VITE_API_BASE_URL` or a backend that is not
 * running, and surfacing that directly saves a trip to the network tab.
 */

import { fetchLiveness, type LivenessResponse } from '@/features/health/api';
import { useQuery } from '@tanstack/react-query';

export const apiStatusQueryKey = ['health', 'liveness'] as const;

export function useApiStatus() {
  return useQuery<LivenessResponse>({
    queryKey: apiStatusQueryKey,
    queryFn: fetchLiveness,
    // The point is to notice an API that went away, so keep it fresh.
    staleTime: 10_000,
    refetchInterval: 30_000,
  });
}
