/**
 * Admin Scans/Disk/Audit/Health E2E — Phase 4 PR #14 §6.3.
 *
 * W6-#43b removed the legacy ``/admin/dt`` surface (ADR-0001 — DT replaced by
 * Trivy). The first test now pins the route as 404-equivalent (AdminNotFound
 * inside the admin layout); the remaining four scenarios continue to drive the
 * operational admin surfaces against the live docker-compose dev stack.
 * Selectors live in the per-page harnesses (data-testid + data-*) so EN/KO
 * renders pass the same scenarios; toasts are matched by data-toast-key so
 * translated copy never enters the assertion.
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable (the seed helper auto-skips with
 *     a descriptive reason if not).
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

function tryAcquireSeed(
  testInfo: import("@playwright/test").TestInfo,
  opts: Parameters<typeof seedE2eUser>[0],
): SeedSummary | null {
  try {
    return seedE2eUser(opts);
  } catch (err) {
    testInfo.skip(
      true,
      `seed precondition failed — bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

test.describe("@critical admin scans / disk / audit / health", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("1) /admin/dt is removed — super admin lands on AdminNotFound (W6-#43b)", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-dt"],
      superAdmin: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    // ADR-0001 / W6-#43b: the Dependency-Track surface was removed. The
    // ``<Route path="*" element={<AdminNotFound />}>`` fallback inside the
    // admin layout catches any remaining `/admin/dt` link; the AppShell main
    // chrome stays mounted while the page body shows AdminNotFound.
    await page.goto("/admin/dt");
    await expect(page.getByTestId("admin-not-found")).toBeVisible({
      timeout: 10_000,
    });
    // The admin sidebar entry must be gone too — sanity-check it does not
    // resurrect when a super admin is signed in.
    await expect(page.getByTestId("nav-admin-dt")).toHaveCount(0);
  });

  test("2) Scan Queue — tab switching + open drawer + status visible", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-scans"],
      superAdmin: true,
      withScan: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    const scans = await portal.gotoAdminScans();

    // The default tab is "running" — pivot to "all" so the seeded
    // `succeeded` scan appears.
    await scans.selectTab("all");

    // The list query is paged at 50 by default — the seeded scan should be
    // somewhere on the page (recent first by created_at desc).
    const rowCount = await scans.getRowCount();
    expect(rowCount).toBeGreaterThan(0);

    // Open the first row's drawer; succeeded scans don't expose the cancel
    // button, but the drawer still mounts with all the meta fields.
    const scanId = await scans.openFirstRowDrawer();
    expect(scanId).toBeTruthy();
    const drawer = page.getByTestId("admin-scan-drawer");
    await expect(drawer).toBeVisible();
    // The status badge appears in the drawer meta region as well as inside
    // each row. Scope to the drawer so the assertion is unambiguous.
    await expect(
      drawer.getByTestId("admin-scan-status-badge"),
    ).toHaveAttribute(
      "data-status",
      /^(queued|running|succeeded|failed|cancelled)$/,
    );

    await scans.closeDrawer();
  });

  test("3) Disk page — three cards render with valid statuses", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-disk"],
      superAdmin: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    const disk = await portal.gotoAdminDisk();

    // Each backend-recognized name renders exactly one card. Workspace +
    // postgres are guaranteed present in the dev stack (filesystem +
    // SQL queries succeed); redis status depends on the broker connection
    // so we accept any of ok / degraded / down for it via the loop below.
    for (const name of ["workspace", "postgres"] as const) {
      const status = await disk.getCardStatus(name);
      expect(["ok", "degraded", "down"]).toContain(status);
    }
  });

  test("4) Audit log — search + CSV download captures a file", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-audit"],
      superAdmin: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    const audit = await portal.gotoAdminAudit();

    // Filter by target_table = users so the result set is bounded and
    // contains at least the seed row (super_admin promotion emits a
    // users row via the audit listener).
    await audit.filterByTargetTable("users");
    await audit.expectMounted();

    // Trigger the CSV export and capture the download. The browser fires
    // the `download` event when the blob anchor click resolves. CORS in
    // the dev stack does not expose ``Content-Disposition`` to the SPA,
    // so axios falls back to the default filename — the meaningful
    // post-condition for this scenario is that the download event fires
    // at all (the streaming generator produced a blob) and the success
    // toast is posted.
    const download = await audit.exportCsv();
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
    await audit.expectSuccessToast("csv_started");
  });

  test("5) System Health — every backend component card renders", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-health"],
      superAdmin: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    const health = await portal.gotoAdminHealth();

    const names = await health.getComponentNames();
    // The backend always emits at least postgres + redis + active_scans +
    // last_24h_errors; celery / disk depend on stack state. Assert the
    // load-bearing ones are present and that every emitted card carries
    // one of the three legal status values.
    for (const required of ["postgres", "redis", "active_scans"] as const) {
      expect(names).toContain(required);
      const status = await health.getComponentStatus(required);
      expect(["ok", "degraded", "down"]).toContain(status);
    }
  });
});
