/**
 * Playwright config dedicated to animated guide walkthroughs.
 *
 * Marathon bundle 9 (4c). Sibling of:
 *   - playwright.config.ts            (e2e)
 *   - playwright.screenshots.config.ts (still PNG-per-step)
 *
 * Walkthrough specs record a webm video of a short, narrated user
 * flow (5–15 seconds) which a postprocess Make target then converts
 * to mp4 (h264 baseline) + a low-FPS gif preview. The original webm
 * is discarded — only the mp4 and gif are committed.
 *
 * Recording at the 1440 × 900 capture viewport keeps the resulting
 * video pixel-aligned with the static PNG captures so docs pages
 * mixing both formats render at the same width.
 *
 * Workers stays at 1 — videos serialize their writes into the
 * Playwright output dir and parallel writes corrupt the WebM
 * container. Auth + seed flow through the same global-setup that
 * the screenshots pipeline uses.
 */
import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";
const STORAGE_STATE_PATH = "./tests/screenshots/.storage-state.json";

export default defineConfig({
  testDir: "./tests/walkthroughs",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [["list"]],
  // Reuse the screenshots pipeline's global setup so both surfaces
  // share one seeded super-admin + one set of projects. The walkthrough
  // specs adopt the same `storageState` and re-inject the access token
  // via `applyAuthFromSeed` to avoid the refresh-token rotation race.
  globalSetup: "./tests/screenshots/global-setup.ts",
  outputDir: "./tests/walkthroughs/.output",
  use: {
    baseURL,
    storageState: STORAGE_STATE_PATH,
    viewport: { width: 1440, height: 900 },
    trace: "off",
    screenshot: "off",
    // ``video: "on"`` writes a webm into ``outputDir/<test>/video.webm``
    // for every test, including passing ones. The postprocess script
    // walks ``outputDir`` and pairs each video with the slug declared
    // in the spec via ``test.info().annotations``.
    video: {
      mode: "on",
      size: { width: 1440, height: 900 },
    },
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
