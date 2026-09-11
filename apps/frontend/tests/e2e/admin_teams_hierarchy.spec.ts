/**
 * Admin Teams: group hierarchy E2E, group-hierarchy Phase 5 PR 5-B.
 *
 * Drives the reparent / create-subgroup flows on ``/admin/teams`` through
 * ``AdminTeamsHarness`` exclusively (no ad-hoc selectors). Written against
 * the harness contract documented at the top of ``AdminTeamsHarness.ts``
 * BEFORE the corresponding ``AdminTeamDrawer`` / ``AdminTeamsPage`` markup
 * exists (this repo's convention for hierarchy specs, see PR 4-B's
 * ``groups.spec.ts``). Not run against a live stack in this PR; verified
 * with ``npx playwright test --list`` only. A later pass (once
 * ``frontend-dev`` implements the drawer's move/subgroup UI) flips these
 * from aspirational to executable.
 *
 * Seed fixtures (``tests/_harness/seed.ts`` / ``seed_e2e_user.py``,
 * group-hierarchy Phase 5 PR 5-B):
 *   - The primary seeded group (``team_id`` / ``team_name``) is always a
 *     root group.
 *   - ``withSubgroup: true`` seeds ONE child group directly under the
 *     primary group (``subgroup``), the real parent/child pair the
 *     cycle-rejection and picker-exclusion scenarios need without driving
 *     the create-subgroup UI first.
 *   - ``extraRootGroup: true`` seeds a second, independent root group
 *     (``extra_root_group``) in the same organization, the "move under a
 *     different root" target.
 *
 * Scenarios (``@critical``, runs on every PR):
 *   1. Create a subgroup under the seeded root group via the UI; open the
 *      child's drawer; its parent badge shows the root group's name.
 *   2. Move the seeded root group under a different (seeded) root; its
 *      parent badge updates to the new root's name, and the list row's
 *      ``data-parent-group-id`` reflects it after reload.
 *   3. Move the seeded subgroup to root (``newParentName: null``); its
 *      parent badge clears, and the list row's ``data-parent-group-id``
 *      clears after reload.
 *   5. The move-target picker excludes the group's own subtree: opening the
 *      seeded parent's drawer, its picker offers the unrelated seeded root
 *      but never the seeded child. (A scenario 4 attempting the same cycle
 *      by selecting the child in the picker used to live here; removed as
 *      unreachable through this guard, see the comment above scenario 5's
 *      definition.)
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable.
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
      `seed precondition failed, bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

test.describe("@critical admin teams: group hierarchy", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("1) create subgroup under the seeded root → child's drawer shows the root as parent", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-hierarchy-create"],
      superAdmin: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const suffix =
      Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    const childName = `QA Subgroup E2E ${suffix}`;
    const childSlug = `qa-subgroup-e2e-${suffix}`;

    const portal = new PortalPage(page);
    const teams = await portal.gotoAdminTeams();

    await teams.expectTeamRow(seed.team_name);
    await teams.openTeamDrawer(seed.team_name);
    await teams.createSubgroup({ name: childName, slug: childSlug });
    await teams.expectSuccessToast("subgroup_created");

    await teams.closeTeamDrawer();
    await teams.expectTeamRow(childName);
    await teams.openTeamDrawer(childName);
    await teams.expectParentBadge(seed.team_name);
  });

  test("2) move the seeded root under a different root → parent badge updates", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-hierarchy-move"],
      superAdmin: true,
      extraRootGroup: true,
    });
    if (seed === null) return;
    expect(seed.extra_root_group).toBeTruthy();
    const newRoot = seed.extra_root_group!;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    const teams = await portal.gotoAdminTeams();

    await teams.expectTeamRow(seed.team_name);
    await teams.openTeamDrawer(seed.team_name);

    await teams.moveGroup(newRoot.name);
    await teams.expectSuccessToast("moved");
    await teams.expectParentBadge(newRoot.name);

    await page.reload();
    await teams.expectMounted();
    await teams.expectRowParent(seed.team_name, newRoot.id);
  });

  test("3) move the seeded subgroup to root → parent badge clears", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-hierarchy-to-root"],
      superAdmin: true,
      withSubgroup: true,
    });
    if (seed === null) return;
    expect(seed.subgroup).toBeTruthy();
    const child = seed.subgroup!;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    const teams = await portal.gotoAdminTeams();

    await teams.expectTeamRow(child.name);
    await teams.openTeamDrawer(child.name);

    await teams.moveGroup(null);
    await teams.expectSuccessToast("moved");
    await teams.expectParentBadge(null);

    await page.reload();
    await teams.expectMounted();
    await teams.expectRowParent(child.name, null);
  });

  // Scenario 4 (this same PR's own picker-exclusion guard, scenario 5 below)
  // used to live here: open the parent's drawer, select the seeded child as
  // the move target, and assert the server's `cycle_detected` rejection.
  // Both landed in PR #464, and the picker guard makes the scenario
  // unreachable through the harness it was written against:
  // `AdminTeamsHarness.moveGroup` requires the target option to exist
  // (`toHaveCount(1)`) before it can select it, and scenario 5 below proves
  // the excluded child is never one of those options. The nightly e2e run
  // caught this the day after #464 merged (#392); PR-blocking checks don't
  // run this suite (CONTRIBUTING.md), so it went out unnoticed.
  //
  // Removed rather than rewritten: the server-side rejection this asserted
  // is covered independently at the API layer
  // (apps/backend/tests/integration/test_group_reparent.py,
  // test_admin_teams_api.py), and the client-side guard that makes this
  // scenario unreachable is exactly scenario 5's own assertion. Keeping both
  // would mean asserting the same guarantee twice through two different
  // paths for no additional coverage.

  test("5) move-target picker excludes the group's own subtree", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-hierarchy-picker"],
      superAdmin: true,
      withSubgroup: true,
      extraRootGroup: true,
    });
    if (seed === null) return;
    expect(seed.subgroup).toBeTruthy();
    expect(seed.extra_root_group).toBeTruthy();
    const otherRoot = seed.extra_root_group!;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    const teams = await portal.gotoAdminTeams();

    // Open the PARENT's drawer and assert its picker offers the unrelated
    // seeded root as a candidate but never its own seeded child
    // (`seed.subgroup.name`), proving the exclusion is subtree-scoped, not
    // "hide every option". The child's absence is asserted by requiring the
    // options list equal exactly `[otherRoot.name]`.
    await teams.expectTeamRow(seed.team_name);
    await teams.openTeamDrawer(seed.team_name);
    await teams.expectMoveTargetOptions([otherRoot.name]);
  });
});
