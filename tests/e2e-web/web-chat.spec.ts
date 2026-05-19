/**
 * End-to-end tests for the Hermes user-facing web chat (port 9120).
 *
 * Covers the happy path a real user would walk:
 *   1. Land on / → redirected to /login
 *   2. Sign in with admin / darling
 *   3. Land on /chat with the user chip visible
 *   4. Send a message; observe the assistant bubble stream content
 *   5. Open the skill drawer, toggle a skill, see the override flag
 *   6. Sign out → back to /login
 *
 * Failure surface: missing UI elements, broken streaming, or any
 * unhandled exception in the dev console (we attach a listener).
 */
import { expect, test } from "@playwright/test";

const ADMIN_HANDLE = process.env.ADMIN_HANDLE ?? "admin";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD ?? "darling";

async function signIn(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await page.locator("input[autocomplete='username']").fill(ADMIN_HANDLE);
  await page.locator("input[type='password']").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/chat$/, { timeout: 15_000 });
}

test.describe("web-chat", () => {
  test.beforeEach(async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("pageerror", (err) => consoleErrors.push(String(err)));
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    // Stash for later assertions
    (page as unknown as { _hermesErrors: string[] })._hermesErrors =
      consoleErrors;
  });

  test("rejects wrong password with inline error", async ({ page }) => {
    await page.goto("/login");
    await page.locator("input[autocomplete='username']").fill(ADMIN_HANDLE);
    await page.locator("input[type='password']").fill("wrong-password-xyz");
    await page.getByRole("button", { name: /sign in/i }).click();
    // Stays on /login and shows an error message
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.locator("body")).toContainText(/401|invalid|fail/i);
  });

  test("signs in and shows user chip + sign out button", async ({ page }) => {
    await signIn(page);
    await expect(page.getByText(new RegExp(`@${ADMIN_HANDLE}`))).toBeVisible();
    await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible();
  });

  test("sends a message and renders a streaming reply", async ({ page }) => {
    await signIn(page);
    const textarea = page.locator("textarea");
    await textarea.fill("Reply with only one word: STREAM_OK");
    await page.getByRole("button", { name: /send/i }).click();
    // The user bubble appears immediately
    await expect(page.locator("text=STREAM_OK").first()).toBeVisible({
      timeout: 5_000,
    });
    // The assistant bubble eventually contains text (model latency budget)
    await expect(page.locator("text=STREAM_OK").nth(1)).toBeVisible({
      timeout: 45_000,
    });
  });

  test("opens the skill drawer and toggles a skill", async ({ page }) => {
    await signIn(page);
    await page.getByRole("button", { name: /skills/i }).click();
    // Aside titled "Skills" appears
    await expect(page.getByRole("heading", { name: /skills/i })).toBeVisible();
    // Flip the first toggle. The toggle has role=switch.
    const firstToggle = page.locator("button[role='switch']").first();
    const wasChecked = (await firstToggle.getAttribute("aria-checked")) === "true";
    await firstToggle.click();
    await expect(firstToggle).toHaveAttribute(
      "aria-checked",
      String(!wasChecked),
    );
    // Override badge is visible somewhere in the drawer
    await expect(page.locator("text=/per-user override/i")).toBeVisible();
  });

  test("sign out clears storage and bounces to login", async ({ page }) => {
    await signIn(page);
    await page.getByRole("button", { name: /sign out/i }).click();
    await expect(page).toHaveURL(/\/login$/);
    const stored = await page.evaluate(() =>
      window.localStorage.getItem("hermes.access_token"),
    );
    expect(stored).toBeNull();
  });

  test.afterEach(async ({ page }) => {
    const errors = (page as unknown as { _hermesErrors?: string[] })
      ._hermesErrors;
    // Ignore well-known noisy errors we don't own (e.g. browser
    // extensions injecting into the page when running --headed).
    const filtered = (errors ?? []).filter(
      (e) => !/extension|favicon|sandbox/i.test(e),
    );
    expect(filtered, `unhandled console errors: ${filtered.join("\n")}`).toEqual(
      [],
    );
  });
});
