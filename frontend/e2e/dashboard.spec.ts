import { expect, test, type Page } from '@playwright/test';

/** Signed-in navigation across the dashboard, against the real API. */

const PASSWORD = 'an-e2e-test-password';

async function registerAndSignIn(page: Page): Promise<void> {
  const email = `e2e-${Date.now()}-${Math.floor(Math.random() * 10_000)}@example.com`;
  await page.goto('/register');
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: /create account/i }).click();
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 20_000 });
}

/**
 * The one account that holds the mock GitHub identity.
 *
 * A GitHub identity can be linked to exactly one DevPilot account -- that is a
 * security property, not a limitation -- and mock mode has exactly one
 * identity. So tests that need GitHub cannot each register a fresh account and
 * all claim it: the second would be refused with 409, correctly. They share
 * this account instead, exactly as one real person with one GitHub login
 * would. It persists across runs, so the helper signs in if it already exists.
 */
const GITHUB_USER_EMAIL = 'e2e-github-owner@example.com';

async function signInAsGitHubOwner(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByLabel('Email').fill(GITHUB_USER_EMAIL);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: /sign in/i }).click();

  // First run on a fresh database: the account does not exist yet.
  const failed = page.getByText(/incorrect email or password/i);
  const landed = page.waitForURL(/\/dashboard/, { timeout: 20_000 });
  await Promise.race([landed, failed.waitFor({ timeout: 20_000 })]);
  if (await failed.isVisible().catch(() => false)) {
    await page.goto('/register');
    await page.getByLabel('Email').fill(GITHUB_USER_EMAIL);
    await page.getByLabel('Password').fill(PASSWORD);
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 20_000 });
  }
}

/**
 * Link the account's GitHub identity through the real OAuth round trip.
 *
 * Required before a sync: an installation can only be synced by the user whose
 * linked identity owns it. In mock mode the authorize URL points back at the
 * API's own callback, so this completes with no GitHub account -- but it still
 * goes through the signed-state check, which is the part worth exercising.
 * Idempotent, because the owner account keeps its link between runs.
 */
async function connectGitHub(page: Page): Promise<void> {
  await page.goto('/settings');
  // The page loads status asynchronously; deciding before it resolves would
  // read "not connected" on every visit and try to click a button that never
  // appears. Wait for either outcome, then branch.
  const connected = page.getByText(/connected as/i);
  const notConnected = page.getByText(/no github account connected/i);
  await expect(connected.or(notConnected)).toBeVisible({ timeout: 20_000 });
  if (await connected.isVisible()) {
    return;
  }
  await page.getByRole('button', { name: /connect github/i }).click();
  await expect(page).toHaveURL(/\/settings\?github=linked/, { timeout: 20_000 });
  await expect(page.getByText(/connected as/i)).toBeVisible();
}

test.describe('dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await registerAndSignIn(page);
  });

  test('a new account sees an empty state that says what to do next', async ({
    page,
  }) => {
    // "Nothing here" alone leaves a new user stuck on their first screen.
    await expect(page.getByText(/no reviews yet/i)).toBeVisible();
    await expect(page.getByRole('link', { name: /go to repositories/i })).toBeVisible();
  });

  test('syncing is refused until GitHub is connected', async ({ page }) => {
    // The installation belongs to a GitHub identity; an account that has not
    // linked one cannot claim it. Before this rule, any signed-in user could
    // sync any installation id they could guess.
    await page.goto('/repositories');
    await expect(page.getByRole('button', { name: /sync from github/i })).toHaveCount(0);
    await expect(page.getByText(/connect github/i).first()).toBeVisible();
  });

  test('settings shows the account and its GitHub connection', async ({ page }) => {
    await page
      .getByRole('navigation', { name: 'Primary' })
      .getByRole('link', { name: 'Settings' })
      .click();

    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
    await expect(page.getByText(/no github account connected/i)).toBeVisible();
  });

  test('an unknown URL shows the not-found page', async ({ page }) => {
    await page.goto('/this-route-does-not-exist');

    await expect(page.getByText(/page not found/i)).toBeVisible();
  });
});

/**
 * Everything that needs a linked GitHub identity.
 *
 * Kept apart from the fresh-account tests above: these share the single
 * owner account because the mock has a single GitHub identity, and one
 * identity links to one DevPilot account.
 */
test.describe('github-linked account', () => {
  test.beforeEach(async ({ page }) => {
    await signInAsGitHubOwner(page);
  });

  test('the repositories page loads and can sync', async ({ page }) => {
    await connectGitHub(page);

    // Scoped to the nav landmark: the empty state also links to repositories,
    // and an unscoped name match is ambiguous.
    await page
      .getByRole('navigation', { name: 'Primary' })
      .getByRole('link', { name: 'Repositories' })
      .click();

    await expect(page).toHaveURL(/\/repositories/);
    await expect(page.getByRole('heading', { name: 'Repositories' })).toBeVisible();

    // Mock mode serves a demo installation owned by the identity just linked.
    await page
      .getByRole('button', { name: /sync from github/i })
      .first()
      .click();

    await expect(page.getByText(/devpilot-demo\//).first()).toBeVisible({
      timeout: 20_000,
    });
  });

  test('a synced repository opens its detail page', async ({ page }) => {
    await connectGitHub(page);
    await page.goto('/repositories');
    await page
      .getByRole('button', { name: /sync from github/i })
      .first()
      .click();

    const repository = page.getByRole('link', { name: /devpilot-demo\// }).first();
    await expect(repository).toBeVisible({ timeout: 20_000 });
    await repository.click();

    await expect(page).toHaveURL(/\/repositories\/[0-9a-f-]{36}/);
  });

  test('connecting GitHub completes the OAuth round trip', async ({ page }) => {
    await connectGitHub(page);
    await expect(page.getByText(/devpilot-demo/)).toBeVisible();
  });
});
