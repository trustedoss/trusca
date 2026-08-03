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
    // Tolerance, measured rather than guessed.
    //
    // The bootstrap value was 0.15 — up to 15 % of the viewport allowed to
    // differ. That is roughly the width of the sidebar: a change big enough
    // to redesign a whole region could pass. It was never justified against
    // data, only described as "generous".
    //
    // Measured 2026-07-28 by capturing the eight baselines twice from the
    // same commit and diffing: the run-to-run floor is 0.0041 (worst screen,
    // project-detail-vulnerabilities), and it comes almost entirely from the
    // seeded project and team names, which carry a timestamp. login.png,
    // which renders no seeded data, was byte-identical.
    //
    // 0.02 leaves ~5x headroom over that floor while catching anything
    // larger than about a 160x160 block. Re-measure before moving it again;
    // a threshold nobody measured is how the last one ended up 37x looser
    // than the noise it was meant to absorb.
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.02,
      threshold: 0.2,
      animations: "disabled",
      caret: "hide",
    },
  },
  // Drop ``-{projectName}-{platform}`` from the snapshot filename
  // — we only ever run on chromium-linux in CI, and the platform
  // tag would otherwise cause every operator's macOS/Windows local
  // run to write a divergent baseline. The single platform-less
  // baseline lives in git, and the ui-gates workflow's
  // ``update_baselines`` dispatch input is the only sanctioned way to
  // rewrite it. (This comment used to name a
  // ``visual-regression-update-baselines`` label that was never built.)
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
