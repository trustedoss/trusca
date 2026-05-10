/**
 * Playwright config dedicated to guide-screenshot capture.
 *
 * Lives alongside `playwright.config.ts` (the e2e suite) but targets
 * `tests/screenshots/` instead of `tests/e2e/`. Two separate configs keep
 * the matrices independent: the e2e CI matrix never triggers a capture
 * accidentally, and `make screenshots-capture` never runs the unrelated
 * e2e suite.
 *
 * Viewport is fixed at 1440 × 900 (Macbook standard). Workers stays at 1
 * so a single seeded user fixture is shared across every spec — no
 * cross-test interference and no rate-limit churn against the auth
 * backend.
 *
 * The PNG output path is computed at spec-time and writes directly into
 * `docs-site/static/img/screenshots/` so the resulting Markdown
 * references (`/img/screenshots/<file>.png`) are EN/KO-shared without an
 * extra copy step.
 */
import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: "./tests/screenshots",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "off",
    video: "off",
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
});
