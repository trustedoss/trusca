/**
 * Playwright config dedicated to visual regression checks.
 *
 * Marathon bundle 9 (4b). Sibling of:
 *   - playwright.config.ts                (e2e)
 *   - playwright.screenshots.config.ts    (guide PNG capture)
 *   - playwright.walkthroughs.config.ts   (mp4/gif walkthroughs)
 *
 * Why a fourth config rather than another folder under tests/e2e/?
 * Visual regression has its own tolerance settings (anti-aliasing,
 * pixel-diff threshold, max different pixels) that we do NOT want
 * the e2e matrix inheriting. A miss-configured threshold on the
 * visual side must not turn the e2e suite flaky.
 *
 * Auth + seeded data: reuses the screenshots pipeline's globalSetup
 * so the same super-admin + two projects are available without
 * re-seeding. The visual spec adopts ``storageState`` and re-injects
 * the access token via ``applyAuthFromSeed`` to avoid the refresh-
 * token rotation race.
 *
 * Baselines: stored under ``tests/visual/visual.spec.ts-snapshots/``
 * via the default ``snapshotPathTemplate``. Operators update them
 * with ``npx playwright test --config=playwright.visual.config.ts
 * --update-snapshots`` after intentional UI changes; the diff is
 * surfaced in the PR via the workflow's artifact upload.
 */
import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";
const STORAGE_STATE_PATH = "./tests/screenshots/.storage-state.json";

export default defineConfig({
  testDir: "./tests/visual",
  timeout: 60_000,
  expect: {
    timeout: 10_000,
    // Tolerance tuning — these numbers are derived from observed
    // steady-state pixel drift between two captures of the same page
    // taken seconds apart on the same machine (font-hinting variance
    // + sub-pixel anti-alias). 0.15 maxDiffPixelRatio caps the diff
    // at ~15 % of the viewport, which is generous for "the layout
    // changed" but tight enough that a wholesale page redesign trips
    // the gate. threshold=0.2 (per-pixel color delta in RGB space)
    // matches Playwright's documented baseline for cross-OS captures.
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.15,
      threshold: 0.2,
      animations: "disabled",
      caret: "hide",
    },
  },
  // Drop ``-{projectName}-{platform}`` from the snapshot filename
  // — we only ever run on chromium-linux in CI, and the platform
  // tag would otherwise cause every operator's macOS/Windows local
  // run to write a divergent baseline. The single platform-less
  // baseline lives in git and the workflow's --update-snapshots
  // step (gated by the ``visual-regression-update-baselines``
  // label) is the only sanctioned way to rewrite it.
  snapshotPathTemplate: "{testDir}/{testFileName}-snapshots/{arg}{ext}",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [["list"], ["html", { outputFolder: "./tests/visual/.report", open: "never" }]],
  globalSetup: "./tests/screenshots/global-setup.ts",
  outputDir: "./tests/visual/.output",
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
