/**
 * Typed, validated access to build-time configuration.
 *
 * Vite inlines `import.meta.env` at build time, so a missing or malformed
 * value silently becomes `undefined` and fails much later as a confusing
 * network error. Validating once, here, turns that into an immediate and
 * explicit failure.
 */

function readRequired(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `Missing environment variable ${name}. Copy .env.example to .env and set it.`,
    );
  }
  return value;
}

/**
 * Base URL of the DevPilot API, as reachable from the browser.
 *
 * `/` means "the origin this page was loaded from", for deployments where the
 * API serves the frontend itself. It becomes an empty prefix, so requests go
 * to `/api/v1/...` on the current host -- no CORS, and nothing to rebuild when
 * the hostname changes.
 */
function apiBaseUrl(raw: string): string {
  const value = raw.trim();
  if (value === '/') return '';
  return value.replace(/\/$/, '');
}

export const env = {
  apiBaseUrl: apiBaseUrl(
    readRequired(
      'VITE_API_BASE_URL',
      import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000',
    ),
  ),
} as const;
