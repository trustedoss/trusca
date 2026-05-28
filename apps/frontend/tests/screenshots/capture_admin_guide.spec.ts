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
 *   - audit-log
 *   - disk-and-health
 *   - api-keys (alias for `/integrations` API keys section — admin
 *     gets org-scoped key visibility there)
 *
 * `backup-and-restore` is already covered by the original PoC in
 * `capture.spec.ts` (PR #53). The legacy `dt-connector` page was dropped
 * with W6-#43b (ADR-0001 — DT replaced by Trivy); the replacement
 * `vulnerability-data` admin guide is captured separately once W6-#43e
 * lands the Trivy DB health panel.
 */
import { test } from "@playwright/test";

import { AdminAuditHarness } from "../_harness/AdminAuditHarness";
import { AdminDiskHarness } from "../_harness/AdminDiskHarness";
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

  // Click the first audit row to slide in the diagnostic drawer with the
  // full sanitised diff (target table, action, request_id, and the
  // key/value JSON diff list). The dev DB has hundreds of audit entries
  // accumulated from prior seed runs, so a row is guaranteed to be
  // present without an extra seed call. The drawer's diff section is the
  // load-bearing element for the docs reference, so we wait for it
  // explicitly before capture.
  test("admin-audit-row-diff — drawer with sanitised diff JSON panel", async ({
    page,
  }) => {
    const audit = new AdminAuditHarness(page);
    await page.goto("/admin/audit");
    await audit.expectMounted();
    await audit.expectRowVisible();
    await audit.openFirstRowDrawer();
    await page
      .getByTestId("admin-audit-drawer-diff")
      .waitFor({ state: "visible", timeout: 10_000 });
    await captureScreenshot(page, "admin-audit-row-diff");
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
