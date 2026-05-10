/**
 * Guide-screenshot capture — admin/backup PoC.
 *
 * Produces the four PNG assets referenced by the EN + KO admin backup
 * guide under `docs-site/static/img/screenshots/`. This file is the
 * proof-of-concept that established the capture pipeline (Session 1
 * PR #53). Per-page bulk specs live alongside (e.g.
 * `capture_user_guide.spec.ts`).
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
 * Adding a new page-level capture: prefer a new `*_spec.ts` per page
 * group rather than growing this file (see `capture_user_guide.spec.ts`).
 */
import { expect, test } from "@playwright/test";

import { AdminBackupHarness } from "../_harness/AdminBackupHarness";
import { AuthHarness } from "../_harness/auth";
import { type SeedSummary } from "../_harness/seed";
import {
  captureScreenshot,
  withSeedBeforeAll,
} from "./_helpers";

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

test.describe.serial("@screenshots admin/backup", () => {
  let seed: SeedSummary | null = null;

  withSeedBeforeAll("admin-backup", ["screenshots-admin-backup"], (s) => {
    seed = s;
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
    await captureScreenshot(page, "admin-backup-list");
  });

  test("admin-backup-trigger-toast — toast shown right after manual trigger", async ({
    page,
  }) => {
    const backup = new AdminBackupHarness(page);
    await backup.gotoBackup();
    await backup.triggerManualBackup();
    await captureScreenshot(page, "admin-backup-trigger-toast");
  });

  test("admin-backup-restore-modal — restore strip + warning panel", async ({
    page,
  }) => {
    const backup = new AdminBackupHarness(page);
    await backup.gotoBackup();
    await backup.openRestoreModal(SENTINEL_BACKUP_FILE);
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
    await expect(
      page.getByTestId("admin-backup-restore-confirm"),
    ).toHaveValue("restore");
    await captureScreenshot(page, "admin-backup-restore-typing-gate-enabled");
  });
});
