/**
 * Playwright configuration — Phase 1 PR #6 (1.9).
 *
 * Targets the locally running docker-compose dev stack (`docker-compose
 * -f docker-compose.dev.yml up`). No `webServer` block: bringing the stack up
 * inside Playwright would conflict with the long-running dev containers and
 * the browser is talking to real Postgres + Redis + FastAPI either way.
 *
 * Single worker by design — the auth backend rate-limits at 5 login attempts
 * per IP per minute, so parallel runs would mutually 429 each other. Once
 * the limiter is per-test-IP (Phase 2+) we can revisit.
 */
import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: "./tests/e2e",
  // ux-audit/ holds capture scripts that drive the harness through every
  // surface to dump screenshots into ./tests/.captures — they are not
  // regression tests and would not be expected to pass on a fresh
  // headless CI workspace. Exclude them from the e2e gate; run them
  // manually via `npx playwright test tests/e2e/ux-audit` when the goal
  // is to refresh capture artifacts.
  testIgnore: ["**/ux-audit/**"],
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [["github"], ["list"]]
    : [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
