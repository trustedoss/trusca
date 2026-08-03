/**
 * Playwright config for the accessibility gate.
 *
 * Sibling of playwright.visual.config.ts and shares its globalSetup, so
 * the same seeded super-admin and projects are available without a second
 * seed pass. Separate config rather than a folder in the e2e suite for the
 * same reason the visual one is separate: its pass/fail criteria are its
 * own, and a change here must not be able to loosen the e2e matrix.
 *
 * No screenshot tolerance settings — this config compares rule counts, not
 * pixels.
 */
import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";
const STORAGE_STATE_PATH = "./tests/screenshots/.storage-state.json";

export default defineConfig({
  testDir: "./tests/a11y",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [["list"]],
  globalSetup: "./tests/screenshots/global-setup.ts",
  outputDir: "./tests/a11y/.output",
  use: {
    baseURL,
    storageState: STORAGE_STATE_PATH,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "off",
    video: "off",
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
