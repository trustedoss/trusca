/**
 * Approvals surface E2E (B2).
 *
 * Three things the queue could not do before:
 *
 *   1. Be linked to. Opening a row left the address bar untouched, so the
 *      view could not be shared or reloaded and the browser's Back button
 *      left the page instead of closing the drawer.
 *   2. Be scoped to a project. The governance band on a project page counts
 *      that project's open approvals and linked to the whole portfolio's
 *      queue, where the number the user had just clicked appeared nowhere.
 *   3. Name the requester. The column printed the first eight characters of
 *      a user id.
 *
 * Pre-requisites (auto-skip otherwise), as the other authenticated specs:
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 *
 * Tagged `@approvals-surface`.
 */
import { expect, test } from "@playwright/test";

import { ApprovalsHarness } from "../_harness/ApprovalsHarness";
import { AuthHarness } from "../_harness/auth";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

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
      `seed precondition failed: bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

test.describe("@approvals-surface the queue is addressable", () => {
  test.beforeEach(async ({ page }) => {
    await new AuthHarness(page).clearAuthState();
  });

  test("the open drawer is in the URL, and Back closes it", async ({
    page,
  }, testInfo) => {
    const prefix = `b2drawer${runNonce()}`;
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["b2-approvals-drawer"],
      withScan: true,
      componentCount: 3,
      componentPrefix: prefix,
      withRefreshToken: true,
      extraMembers: 1,
      extraTeamAdmin: true,
    });
    if (seed === null) return;

    const approvals = new ApprovalsHarness(page);
    const teamAdmin = seed.extra_members![0];
    const token = await approvals.apiLogin(teamAdmin.email, seed.password);
    const projectId = seed.project_ids[0];
    const componentIds = await approvals.apiListComponentIds(token, projectId);
    expect(componentIds.length).toBeGreaterThanOrEqual(1);
    await approvals.apiCreateApproval(token, componentIds[0], projectId);

    await new AuthHarness(page).loginViaRefreshCookie(seed.refresh_token!.token);
    await approvals.gotoApprovals();

    await approvals.openFirstRowDrawer();
    await approvals.expectDrawerInUrl();

    // Reload: the drawer has to come back, or the address was decoration.
    await page.reload();
    await expect(page.getByTestId("approvals-drawer")).toBeVisible();

    await approvals.closeDrawerWithBrowserBack();
  });

  test("a project-scoped queue says so, names the requester, and can be widened", async ({
    page,
  }, testInfo) => {
    const prefix = `b2scope${runNonce()}`;
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["b2-approvals-scope-a", "b2-approvals-scope-b"],
      withScan: true,
      componentCount: 3,
      componentPrefix: prefix,
      withRefreshToken: true,
      extraMembers: 1,
      extraTeamAdmin: true,
    });
    if (seed === null) return;

    const approvals = new ApprovalsHarness(page);
    const teamAdmin = seed.extra_members![0];
    const token = await approvals.apiLogin(teamAdmin.email, seed.password);

    // One approval in each project, so a filter that does nothing would be
    // indistinguishable from one that works.
    const [projectA, projectB] = seed.project_ids;
    const compsA = await approvals.apiListComponentIds(token, projectA);
    const compsB = await approvals.apiListComponentIds(token, projectB);
    expect(compsA.length).toBeGreaterThanOrEqual(1);
    expect(compsB.length).toBeGreaterThanOrEqual(1);
    await approvals.apiCreateApproval(token, compsA[0], projectA);
    await approvals.apiCreateApproval(token, compsB[0], projectB);

    await new AuthHarness(page).loginViaRefreshCookie(seed.refresh_token!.token);

    await approvals.gotoApprovals();
    await expect.poll(() => approvals.getRowCount()).toBe(2);

    await approvals.gotoApprovalsForProject(projectA);
    await expect.poll(() => approvals.getRowCount()).toBe(1);
    await approvals.expectProjectFilterVisible();

    // The requester is the team admin who raised it over REST, by name. The
    // seed gives extra members a full_name ("E2E Extra User 0"), so that is
    // what the column must show, and the id must not appear at all.
    const row = page.getByTestId("approvals-row").first();
    await expect(row).toContainText("E2E Extra User");
    await expect(row).not.toContainText(teamAdmin.user_id.slice(0, 8));

    await approvals.clearProjectFilter();
    await expect.poll(() => approvals.getRowCount()).toBe(2);
  });
});
