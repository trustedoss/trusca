/**
 * Guide-screenshot capture — admin/backup PoC.
 *
 * Produces the four PNG assets referenced by the EN + KO admin backup
 * guide under `docs-site/static/img/screenshots/`. This file is the
 * proof-of-concept that established the capture pipeline (Session 1
 * PR #53). Per-page bulk specs live alongside (e.g.
 * `capture_user_guide.spec.ts`).
 *
 * Auth is no longer per-test — `playwright.screenshots.config.ts`
 * adopts the storage state produced by `global-setup.ts`, so every
 * test starts already logged-in as the shared super-admin.
 *
 * Hard rules:
 *   - Use the existing harnesses (`AdminBackupHarness`, …) so selectors
 *     stay locale-agnostic. Direct `page.click()` is prohibited.
 *   - One PNG per `test()` so a single failure does not poison the
 *     batch.
 */
import { expect, test } from "@playwright/test";

import { AdminBackupHarness } from "../_harness/AdminBackupHarness";
import { captureScreenshot } from "./_helpers";

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
