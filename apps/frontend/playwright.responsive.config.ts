/**
 * Playwright config for the narrow-viewport gate (G0-6).
 *
 * Fifth sibling of the visual / a11y / screenshots / walkthroughs configs.
 * Separate for the same reason as the others: its pass criteria are its
 * own, and it is the only config that deliberately does NOT run at
 * 1440×900 — the spec sets 390 px itself, and inheriting a desktop
 * viewport from a shared config is exactly how this band went untested in
 * the first place.
 *
 * Shares the screenshots pipeline's globalSetup so the seeded super-admin
 * and projects are available without a second seed pass.
 */
import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";
const STORAGE_STATE_PATH = "./tests/screenshots/.storage-state.json";

export default defineConfig({
  testDir: "./tests/responsive",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [["list"]],
  globalSetup: "./tests/screenshots/global-setup.ts",
  outputDir: "./tests/responsive/.output",
  use: {
    baseURL,
    storageState: STORAGE_STATE_PATH,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      // Viewport intentionally omitted here — the spec owns it.
      use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 } },
    },
  ],
});
