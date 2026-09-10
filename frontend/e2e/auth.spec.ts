import { expect, test } from '@playwright/test';

/**
 * End-to-end authentication.
 *
 * Runs against the real API, so a pass means registration, login, token storage
 * and the protected-route guard all work *together* — not that each was mocked
 * correctly in isolation.
 */

/** Unique per run: the backend enforces one account per email address. */
function uniqueEmail(): string {
  return `e2e-${Date.now()}-${Math.floor(Math.random() * 10_000)}@example.com`;
}

const PASSWORD = 'an-e2e-test-password';

test.describe('authentication', () => {
  test('an unauthenticated visitor is sent to the login page', async ({ page }) => {
    await page.goto('/dashboard');

    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  });

  test('a new user can register and lands on the dashboard', async ({ page }) => {
    await page.goto('/register');

    await page.getByLabel('Email').fill(uniqueEmail());
    await page.getByLabel('Password').fill(PASSWORD);
    await page.getByRole('button', { name: /create account/i }).click();

    await expect(page).toHaveURL(/\/dashboard/, { timeout: 20_000 });
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
  });

  test('a registered user can sign out and back in', async ({ page }) => {
    const email = uniqueEmail();

    await page.goto('/register');
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Password').fill(PASSWORD);
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 20_000 });

    await page.getByRole('button', { name: /sign out/i }).click();
    await expect(page).toHaveURL(/\/login/);

    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Password').fill(PASSWORD);
    await page.getByRole('button', { name: /^sign in$/i }).click();

    await expect(page).toHaveURL(/\/dashboard/, { timeout: 20_000 });
  });

  test('a wrong password is rejected without saying which field was wrong', async ({
    page,
  }) => {
    const email = uniqueEmail();

    await page.goto('/register');
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Password').fill(PASSWORD);
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 20_000 });
    await page.getByRole('button', { name: /sign out/i }).click();

    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Password').fill('the-wrong-password');
    await page.getByRole('button', { name: /^sign in$/i }).click();

    // "Incorrect email or password" — never which one, or an attacker could
    // enumerate which addresses have accounts.
    const alert = page.getByRole('alert');
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(/incorrect email or password/i);
    await expect(page).toHaveURL(/\/login/);
  });

  test('a short password is refused before it reaches the API', async ({ page }) => {
    await page.goto('/register');

    await page.getByLabel('Email').fill(uniqueEmail());
    await page.getByLabel('Password').fill('short');
    await page.getByRole('button', { name: /create account/i }).click();

    await expect(page.getByText(/at least 12 characters/i)).toBeVisible();
    await expect(page).toHaveURL(/\/register/);
  });
});
