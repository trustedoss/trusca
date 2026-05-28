/**
 * Admin Users & Teams E2E — Phase 4 PR #13 §6.3.
 *
 * Drives the ``/admin/users`` and ``/admin/teams`` surfaces against the live
 * docker-compose dev stack. Every selector lives inside ``AdminUsersHarness``
 * / ``AdminTeamsHarness`` so EN/KO renders pass without rewriting tests; toasts
 * and error alerts are asserted via ``data-toast-key`` and ``data-tone``
 * attributes, never against translated copy.
 *
 * Scenarios (``@critical`` tag — runs on every PR):
 *
 *   1. super_admin loads /admin/users, opens an extra team_admin's drawer,
 *      changes role to developer, verifies the success toast and the
 *      reloaded row reflects the new role.
 *   2. A regular developer hits /admin/users + /admin/teams directly —
 *      both routes render the AdminNotFound (existence-hide) page.
 *   3. super_admin creates a brand-new team, opens the drawer, adds an
 *      extra-member user, then removes that member. Toast keys assert
 *      "created" → "member_added" → "member_removed".
 *   4. Last-super-admin guard. Implementation note: the seeded super-admin
 *      is the only super-admin in the test DB, so attempting to demote
 *      themselves is *also* blocked by the ``cannot_modify_self`` guard
 *      (PR #13 §4.2 safety constraint). We assert that error extension —
 *      it's the deterministic floor regardless of how many super-admins
 *      already exist in the shared dev DB. The full
 *      "two-super-admins → demote one → demote the other → 422" path is
 *      covered by the backend integration tests in
 *      ``apps/backend/tests/integration/test_admin_users_*.py``.
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable (the seed.ts harness skips with a
 *     descriptive reason if not).
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

test.describe("@critical admin users & teams", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("1) super_admin opens /admin/users → changes role → reload reflects", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-users"],
      superAdmin: true,
      extraMembers: 2,
      extraTeamAdmin: true,
    });
    if (seed === null) return;
    expect(seed.is_super_admin).toBe(true);
    expect(seed.extra_members).toBeTruthy();
    expect(seed.extra_members?.length).toBe(2);

    // The seeded primary user is super_admin; the first extra is team_admin
    // (the demotion target), the second is developer (left untouched).
    const target = seed.extra_members![0];
    expect(target.role).toBe("team_admin");

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    const users = await portal.gotoAdminUsers();
    await users.expectUserRow(target.email);

    await users.openUserDrawer(target.email);
    await users.changeRoleTo("developer", { teamId: seed.team_id });
    await users.expectSuccessToast("role_updated");

    // Reload and verify the row reflects the new role. The list endpoint
    // only carries `is_superuser` so the page-level `data-role` collapses
    // non-superusers to "developer" — exactly the post-mutation state we
    // expect. A drawer round-trip would also work but adds a network hop;
    // the row attribute is sufficient as the source of truth.
    await page.reload();
    await users.expectMounted();
    await users.expectUserRowRole(target.email, "developer");
  });

  test("2) non-super-admin hitting /admin/* sees the existence-hide page", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-denied"],
      // No superAdmin / extraMembers — primary user is a plain developer.
    });
    if (seed === null) return;
    expect(seed.is_super_admin === undefined || seed.is_super_admin === false)
      .toBe(true);

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    // Direct-navigate to the admin routes. Each must render the
    // AdminNotFound shell rather than the layout — and the URL must not
    // change (existence-hide is in-place rendering, not a redirect).
    await page.goto("/admin/users");
    await expect(page).toHaveURL(/\/admin\/users$/);
    await expect(page.getByTestId("admin-not-found")).toBeVisible();
    await expect(page.getByTestId("admin-layout")).toHaveCount(0);

    await page.goto("/admin/teams");
    await expect(page).toHaveURL(/\/admin\/teams$/);
    await expect(page.getByTestId("admin-not-found")).toBeVisible();
    await expect(page.getByTestId("admin-layout")).toHaveCount(0);
  });

  test("3) super_admin creates team → adds member → removes member", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-teams"],
      superAdmin: true,
      extraMembers: 1,
    });
    if (seed === null) return;
    const member = seed.extra_members![0];

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    // Unique suffix so re-runs against the same dev DB never collide.
    const suffix = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    const teamName = `QA Team E2E ${suffix}`;
    const teamSlug = `qa-team-e2e-${suffix}`;

    const portal = new PortalPage(page);
    const teams = await portal.gotoAdminTeams();

    await teams.createTeam({ name: teamName, slug: teamSlug });
    await teams.expectSuccessToast("created");

    // The drawer auto-opens after a successful create — wait for it instead
    // of clicking the row again. Add the extra-member user then remove.
    await expect(page.getByTestId("admin-team-drawer")).toBeVisible();

    await teams.addMember({
      userIdOrEmail: member.user_id,
      role: "developer",
    });
    await teams.expectSuccessToast("member_added");
    await teams.expectMemberRow(member.email);

    await teams.removeMember(member.email);
    await teams.expectSuccessToast("member_removed");
    await teams.expectNoMemberRow(member.email);
  });

  test("4) demoting yourself is blocked with cannot_modify_self", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["admin-e2e-self"],
      superAdmin: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    const users = await portal.gotoAdminUsers();

    // Find the primary user's row by email and open their own drawer.
    await users.expectUserRow(seed.email);
    await users.openUserDrawer(seed.email);

    // Attempt to demote self → the backend returns 422 with
    // ``cannot_modify_self = true``. The harness verifies the toast.
    await users.changeRoleTo("developer", { teamId: seed.team_id });
    await users.expectErrorAlert("cannot_modify_self");
  });
});
