/**
 * Guide-screenshot capture — Playwright driver.
 *
 * Produces the PNG assets referenced by the EN + KO admin / user / contributor
 * guides under `docs-site/static/img/screenshots/`. Runs in its own
 * `tests/screenshots/` directory so the e2e matrix never triggers it
 * accidentally; `make screenshots-capture` invokes it via
 * `playwright.screenshots.config.ts`.
 *
 * Hard rules:
 *   - Use the existing harnesses (`AdminBackupHarness`, `AuthHarness`, …)
 *     so selectors stay locale-agnostic and we never re-implement the
 *     navigation logic. Direct `page.click()` / `page.locator()` is
 *     prohibited (CLAUDE.md §품질·보안·운영 §4 + test-writer.md).
 *   - One PNG per `test()` so a single failure does not poison the whole
 *     batch. `describe.serial(...)` shares the seeded super-admin across
 *     captures — re-seeding per-test would multiply DB churn for no gain.
 *
 * Viewport: 1440 × 900 (set by the dedicated config). `fullPage: false` so
 * the captured asset is the visible chrome users actually see, not a
 * scroll-sewn collage.
 *
 * Adding a new capture:
 *   1. Drop a new `test("<page-slug-section-slug>", …)` inside the
 *      relevant `describe.serial(...)` block (or add a new block for a
 *      new page).
 *   2. Drive the SPA via the existing harness verbs to the moment you
 *      want to capture.
 *   3. Call `captureScreenshot(page, "<slug>")`. The slug becomes the
 *      basename of the PNG written under
 *      `docs-site/static/img/screenshots/`.
 *   4. Insert `![alt](/img/screenshots/<slug>.png)` into the EN + KO
 *      Markdown for the section. Absolute path (leading `/`) so EN and
 *      KO share the same asset.
 */
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

import { AdminBackupHarness } from "../_harness/AdminBackupHarness";
import { AuthHarness } from "../_harness/auth";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");
const SCREENSHOT_DIR = path.join(
  REPO_ROOT,
  "docs-site",
  "static",
  "img",
  "screenshots",
);

/**
 * Hide dev-only chrome that does not belong in shipped guide assets.
 *
 * The dev SPA mounts `<ReactQueryDevtools/>` which renders a floating
 * bottom-right toggle button. Production builds tree-shake the import
 * (`import.meta.env.DEV` branch), so the docs reader never sees it — but
 * captures taken against the dev stack do, and they leak into the asset.
 *
 * We inject a stylesheet that hides every TanStack Devtools surface
 * (button + open panel) by class prefix. Removing the elements outright
 * would race the Devtools' own re-render cycle; CSS is durable.
 */
async function hideDevOnlyChrome(page: Page): Promise<void> {
  await page.addStyleTag({
    content: `
      .tsqd-parent-container,
      [class*="tsqd-"],
      [aria-label*="React Query" i] {
        display: none !important;
        visibility: hidden !important;
      }
    `,
  });
}

/**
 * Write a viewport screenshot under `docs-site/static/img/screenshots/`.
 *
 * `fullPage: false` keeps the asset bounded to the 1440×900 viewport that
 * runtime users actually see; the alternative (full-page sewn capture)
 * produces tall narrow PNGs that read like printout artefacts in the
 * docs. Dev-only chrome is hidden right before the capture so the asset
 * matches what production users will see.
 */
async function captureScreenshot(page: Page, slug: string): Promise<void> {
  if (!/^[a-z0-9-]+$/.test(slug)) {
    throw new Error(
      `captureScreenshot: slug "${slug}" must be kebab-case ([a-z0-9-]+)`,
    );
  }
  await hideDevOnlyChrome(page);
  const out = path.join(SCREENSHOT_DIR, `${slug}.png`);
  await page.screenshot({ path: out, fullPage: false });
}

/**
 * Sentinel gz buffer (10 bytes — gzip magic + minimal header) accepted
 * far enough by the SPA to mount the restore strip. The backend would
 * reject it with a Problem Details response, but we only capture the
 * pre-submit state where the strip + typing-gate are visible.
 */
const SENTINEL_BACKUP_FILE = {
  name: "fake-backup-2026-05-09-030000.tar.gz",
  mimeType: "application/gzip",
  buffer: Buffer.from([0x1f, 0x8b, 0x08, 0x00, 0, 0, 0, 0, 0, 0]),
};

/**
 * Acquire a seeded super-admin or skip with a friendly error so the
 * capture run never exits non-zero just because the dev stack is not up.
 */
function tryAcquireSeed(
  testInfo: import("@playwright/test").TestInfo,
  opts: Parameters<typeof seedE2eUser>[0],
): SeedSummary | null {
  try {
    return seedE2eUser(opts);
  } catch (err) {
    testInfo.skip(
      true,
      `seed precondition failed — bring docker-compose dev up first: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
    return null;
  }
}

test.describe.serial("@screenshots admin/backup", () => {
  let seed: SeedSummary | null = null;

  // Playwright requires the first beforeAll argument to be an object-
  // destructure pattern (even if empty). ESLint's no-empty-pattern would
  // otherwise reject `({}, testInfo)` — the disable comment threads both.
  // eslint-disable-next-line no-empty-pattern
  test.beforeAll(async ({}, testInfo) => {
    seed = tryAcquireSeed(testInfo, {
      projectNames: ["screenshots-admin-backup"],
      superAdmin: true,
      withScan: true,
      componentCount: 50,
      // Namespaced + timestamped prefix avoids `uq_components_purl`
      // collisions when other seed calls (e.g. e2e suites) leave their
      // own `comp-NN` rows behind, AND avoids self-collision across
      // back-to-back capture runs against the same DB. Determinism is
      // not a goal for the capture pipeline — every run regenerates the
      // assets from scratch.
      componentPrefix: `screenshot-admin-backup-${Date.now()}`,
      vulnerabilityCount: 30,
      withObligations: true,
      withOAuthIdentity: "github",
    });
  });

  test.beforeEach(async ({ page }) => {
    if (seed === null) test.skip(true, "seed not available");
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed!.email, seed!.password);
  });

  test("admin-backup-list — list view with mounted table", async ({ page }) => {
    const backup = new AdminBackupHarness(page);
    await backup.gotoBackup();
    // expectMounted already waited on the table's aria-busy=false. The
    // table renders rows OR an empty card; both are valid "list view"
    // shapes for the docs.
    await captureScreenshot(page, "admin-backup-list");
  });

  test("admin-backup-trigger-toast — toast shown right after manual trigger", async ({
    page,
  }) => {
    const backup = new AdminBackupHarness(page);
    await backup.gotoBackup();
    await backup.triggerManualBackup();
    // The toast is visible (asserted by triggerManualBackup) — capture
    // the page state including the toast strip before it auto-dismisses.
    await captureScreenshot(page, "admin-backup-trigger-toast");
  });

  test("admin-backup-restore-modal — restore strip + warning panel", async ({
    page,
  }) => {
    const backup = new AdminBackupHarness(page);
    await backup.gotoBackup();
    await backup.openRestoreModal(SENTINEL_BACKUP_FILE);
    // Submit must be disabled — typing-gate untouched. Asserting the
    // disabled state guards the asset's accuracy: if a regression flips
    // the gate to default-enabled, the capture would lie.
    await backup.expectRestoreButtonEnabled(false);
    await captureScreenshot(page, "admin-backup-restore-modal");
  });

  test("admin-backup-restore-typing-gate-enabled — Submit unlocked after typing 'restore'", async ({
    page,
  }) => {
    const backup = new AdminBackupHarness(page);
    await backup.gotoBackup();
    await backup.openRestoreModal(SENTINEL_BACKUP_FILE);
    await backup.typeRestoreConfirm("restore");
    await backup.expectRestoreButtonEnabled(true);
    // Smoke-check the visible textfield value mirrors the gate state.
    await expect(
      page.getByTestId("admin-backup-restore-confirm"),
    ).toHaveValue("restore");
    await captureScreenshot(page, "admin-backup-restore-typing-gate-enabled");
  });
});
