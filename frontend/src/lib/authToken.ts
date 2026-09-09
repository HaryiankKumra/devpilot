/**
 * Access-token storage.
 *
 * The token lives in `localStorage` so a page refresh does not sign the user
 * out. That is a deliberate tradeoff, not an oversight: `localStorage` is
 * readable by any script running on the page, so a successful XSS can steal the
 * token. The alternative -- keeping it only in memory -- needs a refresh token
 * in an httpOnly cookie to survive a reload, which DevPilot does not have yet.
 *
 * What makes it acceptable for now is that access tokens are short-lived (60
 * minutes by default), so a stolen one expires on its own. See
 * docs/engineering-tradeoffs.md; Milestone 11 revisits this.
 *
 * A module-level cache backs the reads because `localStorage` throws in some
 * browser configurations (Safari private mode, third-party cookies disabled),
 * and every read is on the request path.
 */

const STORAGE_KEY = 'devpilot.access_token';

let cachedToken: string | null | undefined;

/** Listeners notified when the token changes, so React state can follow it. */
const subscribers = new Set<() => void>();

export function getAccessToken(): string | null {
  if (cachedToken === undefined) {
    try {
      cachedToken = window.localStorage.getItem(STORAGE_KEY);
    } catch {
      // Storage unavailable: fall back to in-memory only for this page load.
      cachedToken = null;
    }
  }
  return cachedToken;
}

export function setAccessToken(token: string | null): void {
  cachedToken = token;
  try {
    if (token === null) {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, token);
    }
  } catch {
    // Non-fatal: the in-memory cache still serves this session.
  }
  subscribers.forEach((notify) => notify());
}

/** Subscribe to token changes. Returns an unsubscribe function. */
export function subscribeToToken(listener: () => void): () => void {
  subscribers.add(listener);
  return () => subscribers.delete(listener);
}
