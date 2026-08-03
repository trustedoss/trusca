/**
 * Playwright config dedicated to docs-uat ui steps (5th config, sibling of
 * playwright.config.ts / .screenshots / .visual / .walkthroughs).
 *
 * Driven by `tools/docs-uat/run.mjs`, which spawns
 *   npx playwright test --config=playwright.docs-uat.config.ts
 * after it has already brought the dev stack up and seeded the demo dataset
 * via the *documented* commands. So unlike the screenshots pipeline there is
 * NO globalSetup that seeds — the data the spec asserts against is whatever
 * the doc's own `seed_demo` step produced. The spec logs in fresh with the
 * demo account named in the doc (a single login — well under the 5/min/IP
 * rate limit, so no storage-state dance needed).
 *
 * The spec reads the docs-uat manifest (DOCS_UAT_MANIFEST) and dispatches the
 * ui-kind steps for the requested doc/tier against the existing PortalPage /
 * AuthHarness verbs — the docs ARE the tests.
 */
import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: "./tests/docs-uat",
  // Generous: a single test walks login → dashboard → projects sequentially
  // against the real dev stack.
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
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
