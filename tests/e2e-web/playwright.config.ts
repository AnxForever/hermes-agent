import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the Hermes web demo smoke tests.
 *
 * The two URLs default to the docker-compose host mappings:
 *   user chat (nginx)   → http://127.0.0.1:9120
 *   admin dashboard     → http://127.0.0.1:9119
 *
 * Override via env vars when running against a remote deploy:
 *   HERMES_WEB_CHAT_URL=https://chat.example.com \
 *   HERMES_DASHBOARD_URL=https://admin.example.com \
 *   npx playwright test
 *
 * The tests assume an ``admin/darling`` account exists (the default
 * bootstrap from docker-compose). Override with ADMIN_HANDLE /
 * ADMIN_PASSWORD if different.
 */
export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  timeout: 60_000,
  fullyParallel: false, // tests share a backend; serialize to avoid races
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.HERMES_WEB_CHAT_URL ?? "http://127.0.0.1:9120",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
