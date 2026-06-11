/**
 * FE regression smoke — PR-6 (testing-standards hardening, gap 3-1).
 *
 * The verification team's 70-finding sweep exposed 13 frontend-visible
 * defects; the P7 wave (#358–#366) fixed them, but nothing in the automated
 * suite pinned the fixed surfaces. This spec codifies the three exposures
 * the team flagged as core, so a regression fails nightly CI loudly:
 *
 *   M-13 — `/approvals` lands on the compound "open" status filter
 *          (pending + under_review). Before the fix it landed on "all" and
 *          disposed rows buried the actionable queue.
 *   M-20 — the component drawer renders its license Obligations section
 *          (the section silently vanished while the data stayed on the wire).
 *   M-21 — the Compliance tab carries the NOTICE download toolbar
 *          (the affordance was dropped in the W9-#58 grid unification and
 *          was reachable only via the Reports tab).
 *
 * Auth strategy: every test seeds a fresh user with a pre-minted refresh
 * token and enters via `AuthHarness.loginViaRefreshCookie` — NOT the
 * `/auth/login` form (rate-limited 5/min/IP; a multi-test single-IP run
 * trips it). The single `apiLogin` call in M-13 (the seeded team_admin, who
 * must drive the under_review/approved transitions) stays well under the
 * limit.
 *
 * Seed isolation: `componentPrefix` carries a spec-unique slug AND a
 * per-run nonce — component purls are globally unique
 * (`uq_components_purl`), so a fixed prefix collides not only cross-suite
 * but also on the SECOND local run against a long-lived dev DB.
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 reachable from host PATH (seed.ts validates this)
 *   - the host seed must sign refresh JWTs with the SAME key the backend
 *     container verifies with: run with
 *     `SECRET_KEY=<the backend's key>` (compose dev injects `.env`'s
 *     SECRET_KEY into the container; without the export the host seed falls
 *     back to the dev placeholder and `/auth/refresh` 401s).
 */
import { expect, test } from "@playwright/test";

import { ApprovalsHarness } from "../_harness/ApprovalsHarness";
import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

/** Per-run nonce so purls never collide with a previous run's rows. */
function runNonce(): string {
  return Date.now().toString(36);
}

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

test.describe("@fe-regression verified FE exposures (M-13 / M-20 / M-21)", () => {
  test.beforeEach(async ({ page }) => {
    await new AuthHarness(page).clearAuthState();
  });

  test("M-13) /approvals defaults to the open filter and hides disposed rows", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["fe-smoke-approvals"],
      withScan: true,
      componentCount: 3,
      componentPrefix: `fesmokeap${runNonce()}`,
      extraMembers: 1,
      extraTeamAdmin: true,
      withRefreshToken: true,
    });
    if (seed === null) return;

    const approvals = new ApprovalsHarness(page);

    // Shape the queue over REST as the seeded team_admin (transitions to
    // under_review / approved require team_admin; create only needs
    // developer, so one token covers all three rows):
    //   row A — pending          (open)
    //   row B — under_review     (open)
    //   row C — approved         (disposed — must be hidden by default)
    const teamAdmin = seed.extra_members![0];
    const token = await approvals.apiLogin(teamAdmin.email, seed.password);
    const projectId = seed.project_ids[0];
    const componentIds = await approvals.apiListComponentIds(token, projectId);
    expect(componentIds.length).toBeGreaterThanOrEqual(3);

    await approvals.apiCreateApproval(token, componentIds[0], projectId);
    const b = await approvals.apiCreateApproval(
      token,
      componentIds[1],
      projectId,
    );
    await approvals.apiTransitionApproval(token, b, "under_review");
    let c = await approvals.apiCreateApproval(token, componentIds[2], projectId);
    c = await approvals.apiTransitionApproval(token, c, "under_review");
    await approvals.apiTransitionApproval(token, c, "approved");

    // Enter the SPA as the primary (developer) user and land on /approvals
    // with NO query params — exactly the verified-defect entry path.
    const auth = new AuthHarness(page);
    await auth.loginViaRefreshCookie(seed.refresh_token!.token);
    await approvals.gotoApprovals();

    // The filter select shows "open" and the default is kept out of the URL.
    await approvals.expectStatusFilterValue("open");
    expect(new URL(page.url()).searchParams.get("status")).toBeNull();

    // Only the two open rows render; the approved row is filtered out.
    await expect
      .poll(() => approvals.getRowStatuses(), { timeout: 10_000 })
      .toEqual(expect.arrayContaining(["pending", "under_review"]));
    const openStatuses = await approvals.getRowStatuses();
    expect(openStatuses).toHaveLength(2);
    for (const status of openStatuses) {
      expect(["pending", "under_review"]).toContain(status);
    }

    // Explicit "all" still shows the full team queue, approved included.
    await approvals.setStatusFilter("all");
    await expect
      .poll(() => approvals.getRowStatuses(), { timeout: 10_000 })
      .toHaveLength(3);
    expect(await approvals.getRowStatuses()).toContain("approved");
  });

  test("M-20) the component drawer renders the license Obligations section", async ({
    page,
  }, testInfo) => {
    const prefix = `fesmokeobl${runNonce()}`;
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["fe-smoke-drawer-obligations"],
      withScan: true,
      // 8 components → the 4 seed licenses round-robin across all rows and
      // every license category carries at least one obligation, so ANY row's
      // drawer must show a non-empty Obligations section.
      componentCount: 8,
      componentPrefix: prefix,
      withObligations: true,
      withRefreshToken: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.loginViaRefreshCookie(seed.refresh_token!.token);

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail("fe-smoke-drawer-obligations");
    await portal.selectTab("components");
    await portal.expectComponentsTabReady();

    // Seeded component names are zero-padded: `{prefix}-{i:05d}`.
    await portal.openComponentDrawer(`${prefix}-00000`);
    await portal.expectComponentDrawerObligations(1);
  });

  test("M-21) the Compliance tab carries the NOTICE download toolbar", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["fe-smoke-notice-toolbar"],
      withScan: true,
      componentCount: 4,
      componentPrefix: `fesmokentc${runNonce()}`,
      withObligations: true,
      withRefreshToken: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.loginViaRefreshCookie(seed.refresh_token!.token);

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail("fe-smoke-notice-toolbar");
    // selectLicensesTab = Compliance tab with the Has-obligations toggle off,
    // i.e. the tab's default landing view — where M-21 promises the toolbar.
    await portal.selectLicensesTab();
    await portal.expectComplianceNoticeToolbar();
  });
});
