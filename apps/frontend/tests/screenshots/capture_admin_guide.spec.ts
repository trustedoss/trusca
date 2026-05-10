/**
 * Guide-screenshot capture — admin-guide bulk.
 *
 * One `describe.serial(...)` per docs page. Like `capture_user_guide`,
 * auth is shared via `global-setup.ts` + `use.storageState`, and every
 * test starts already authenticated as the seeded super-admin so the
 * existence-hide guard renders the admin layout (CLAUDE.md §품질·보안 §3).
 *
 * Pages covered (matching `docs-site/docs/admin-guide/*.md`):
 *   - users-and-teams
 *   - dt-connector
 *   - audit-log
 *   - disk-and-health
 *   - api-keys (alias for `/integrations` API keys section — admin
 *     gets org-scoped key visibility there)
 *
 * `backup-and-restore` is already covered by the original PoC in
 * `capture.spec.ts` (PR #53).
 */
import { test } from "@playwright/test";

import { AdminAuditHarness } from "../_harness/AdminAuditHarness";
import { AdminDiskHarness } from "../_harness/AdminDiskHarness";
import { AdminDTHarness } from "../_harness/AdminDTHarness";
import { AdminHealthHarness } from "../_harness/AdminHealthHarness";
import { AdminTeamsHarness } from "../_harness/AdminTeamsHarness";
import { AdminUsersHarness } from "../_harness/AdminUsersHarness";
import { applyAuthFromSeed, captureScreenshot } from "./_helpers";

// ════════════════════════════════════════════════════════════════════
// admin/users-and-teams
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots admin-guide/users-and-teams", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("admin-users-list — admin Users page with seeded super-admin row", async ({
    page,
  }) => {
    const users = new AdminUsersHarness(page);
    await page.goto("/admin/users");
    await users.expectMounted();
    await captureScreenshot(page, "admin-users-list");
  });

  test("admin-teams-list — admin Teams page with the seeded primary team", async ({
    page,
  }) => {
    const teams = new AdminTeamsHarness(page);
    await page.goto("/admin/teams");
    await teams.expectMounted();
    await captureScreenshot(page, "admin-teams-list");
  });
});

// ════════════════════════════════════════════════════════════════════
// admin/dt-connector
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots admin-guide/dt-connector", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("admin-dt-status — DT status card + breaker state", async ({ page }) => {
    const dt = new AdminDTHarness(page);
    await page.goto("/admin/dt");
    await dt.expectMounted();
    await captureScreenshot(page, "admin-dt-status");
  });
});

// ════════════════════════════════════════════════════════════════════
// admin/audit-log
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots admin-guide/audit-log", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("admin-audit-list — Audit log toolbar + table", async ({ page }) => {
    const audit = new AdminAuditHarness(page);
    await page.goto("/admin/audit");
    await audit.expectMounted();
    await captureScreenshot(page, "admin-audit-list");
  });
});

// ════════════════════════════════════════════════════════════════════
// admin/disk-and-health
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots admin-guide/disk-and-health", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("admin-disk-list — Disk usage cards", async ({ page }) => {
    const disk = new AdminDiskHarness(page);
    await page.goto("/admin/disk");
    await disk.expectMounted();
    await captureScreenshot(page, "admin-disk-list");
  });

  test("admin-health-cards — System health four-card overview", async ({
    page,
  }) => {
    const health = new AdminHealthHarness(page);
    await page.goto("/admin/health");
    await health.expectMounted();
    await captureScreenshot(page, "admin-health-cards");
  });
});
