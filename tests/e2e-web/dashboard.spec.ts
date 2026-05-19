/**
 * End-to-end tests for the admin dashboard login surface (port 9119).
 *
 * The dashboard ships in two modes:
 *   - Legacy: the SPA picks up an injected session token; /login.html
 *     is still served but never reached in the normal flow.
 *   - RBAC: HERMES_DASHBOARD_REQUIRE_LOGIN=1 makes the SPA bounce any
 *     401 to /login.html, where the operator enters credentials.
 *
 * These tests target /login.html directly so they work in both modes.
 */
import { expect, test } from "@playwright/test";

const ADMIN_HANDLE = process.env.ADMIN_HANDLE ?? "admin";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD ?? "darling";
const DASHBOARD_URL =
  process.env.HERMES_DASHBOARD_URL ?? "http://127.0.0.1:9119";

test.use({ baseURL: DASHBOARD_URL });

test.describe("admin dashboard", () => {
  test("serves /login.html with the expected form", async ({ page }) => {
    const resp = await page.goto("/login.html");
    expect(resp?.status()).toBe(200);
    await expect(page).toHaveTitle(/Hermes Dashboard/i);
    await expect(page.locator("input[name='handle']")).toBeVisible();
    await expect(page.locator("input[name='password']")).toBeVisible();
  });

  test("wrong credentials show an inline 401 error", async ({ page }) => {
    await page.goto("/login.html");
    await page.locator("input[name='handle']").fill(ADMIN_HANDLE);
    await page.locator("input[name='password']").fill("wrong-pwd-zzz");
    await page.getByRole("button", { name: /sign in/i }).click();
    // Page stays at /login.html, error message visible
    await expect(page).toHaveURL(/\/login\.html$/);
    await expect(page.locator(".error")).toContainText(/invalid|401/i);
  });

  test("correct credentials store JWT and redirect to /", async ({ page }) => {
    await page.goto("/login.html");
    await page.locator("input[name='handle']").fill(ADMIN_HANDLE);
    await page.locator("input[name='password']").fill(ADMIN_PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/(index\.html)?$/, { timeout: 15_000 });
    const jwt = await page.evaluate(() =>
      window.localStorage.getItem("hermes.admin_jwt"),
    );
    expect(jwt).toBeTruthy();
    expect((jwt ?? "").length).toBeGreaterThan(100); // sanity-check signed JWT length
  });

  test("API_SERVER_KEY-authenticated /api/admin/me returns the admin user", async ({
    request,
  }) => {
    // Login via the JSON endpoint so we don't depend on the SPA bundle.
    const loginResp = await request.post("/api/admin/login", {
      data: { handle: ADMIN_HANDLE, password: ADMIN_PASSWORD },
    });
    expect(loginResp.status()).toBe(200);
    const { access_token } = await loginResp.json();
    const meResp = await request.get("/api/admin/me", {
      headers: { Authorization: `Bearer ${access_token}` },
    });
    expect(meResp.status()).toBe(200);
    const me = await meResp.json();
    expect(me.handle).toBe(ADMIN_HANDLE);
    expect(me.role).toBe("admin");
    expect(me.auth_source).toBe("jwt");
  });

  test("missing auth on a gated endpoint returns 401", async ({ request }) => {
    const resp = await request.get("/api/sessions");
    expect(resp.status()).toBe(401);
  });
});
