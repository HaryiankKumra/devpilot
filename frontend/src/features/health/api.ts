/** API binding for the backend health endpoints. */

import { apiFetch } from '@/lib/apiClient';

export interface LivenessResponse {
  status: 'ok' | 'degraded';
  environment: string;
  version: string;
}

export function fetchLiveness(): Promise<LivenessResponse> {
  return apiFetch<LivenessResponse>('/health');
}
