/**
 * Thin wrapper around `fetch` for talking to the DevPilot API.
 *
 * Its whole job is to make failures uniform: the backend reports errors as
 * `{"error": {"code", "message"}}`, and every caller would otherwise
 * re-implement the same status-checking and JSON-parsing dance.
 */

import { env } from '@/lib/env';

/** An error response from the API, or a transport failure reaching it. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    options?: { cause?: unknown },
  ) {
    // Keeping the original failure as `cause` preserves the browser's own
    // diagnostic (CORS, DNS, TLS) that our friendly message would otherwise hide.
    super(message, options);
    this.name = 'ApiError';
  }

  /** True when retrying the same request might succeed. */
  get isTransient(): boolean {
    return this.status === 0 || this.status >= 500;
  }
}

interface ApiErrorBody {
  error?: { code?: string; message?: string };
}

async function readError(response: Response): Promise<ApiError> {
  let code = 'unknown_error';
  let message = response.statusText || 'Request failed';
  try {
    const body = (await response.json()) as ApiErrorBody;
    code = body.error?.code ?? code;
    message = body.error?.message ?? message;
  } catch {
    // A non-JSON error body (a proxy's HTML 502 page, say) is not itself an
    // error worth reporting -- the status code is the useful signal.
  }
  return new ApiError(response.status, code, message);
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${env.apiBaseUrl}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...init.headers,
      },
    });
  } catch (cause) {
    // The request never reached the server: DNS, TLS, CORS or the API is down.
    throw new ApiError(0, 'network_error', 'Could not reach the DevPilot API.', {
      cause,
    });
  }

  if (!response.ok) {
    throw await readError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
