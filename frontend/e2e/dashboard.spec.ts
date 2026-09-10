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

  test('the repositories page loads and can sync', async ({ page }) => {
    // Scoped to the nav landmark: the empty state also links to repositories,
    // and an unscoped name match is ambiguous.
    await page
      .getByRole('navigation', { name: 'Primary' })
      .getByRole('link', { name: 'Repositories' })
      .click();

    await expect(page).toHaveURL(/\/repositories/);
    await expect(page.getByRole('heading', { name: 'Repositories' })).toBeVisible();

    // Mock mode serves a demo installation, so syncing works with no credentials.
    await page
      .getByRole('button', { name: /sync from github/i })
      .first()
      .click();

    await expect(page.getByText(/devpilot-demo\//).first()).toBeVisible({
      timeout: 20_000,
    });
  });

  test('a synced repository opens its detail page', async ({ page }) => {
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
