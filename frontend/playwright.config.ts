import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration.
 *
 * These tests run against the *real* stack — the Compose API, worker and
 * database. A browser test against mocked network responses proves the
 * component renders, which the type checker mostly covers already; the value
 * here is proving a user can register, sign in, and reach their data through
 * the real API.
 *
 * `webServer` starts the Vite dev server if one is not already running, so the
 * suite works both locally and in CI.
 */
export default defineConfig({
  testDir: './e2e',
  // A failing assertion is usually a real bug rather than a flake, so retrying
  // locally would only hide it. CI retries once for genuine network flakiness.
  retries: process.env.CI ? 1 : 0,
  // Serial: the tests share one backend database, and parallel registration
  // would race on the unique email constraint.
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',
  timeout: 30_000,
  expect: { timeout: 10_000 },

  use: {
    baseURL: process.env.DEVPILOT_E2E_BASE_URL ?? 'http://localhost:5173',
    // Only kept for failures: a trace per passing test is megabytes of noise.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],

  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
