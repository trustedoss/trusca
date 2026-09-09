/**
 * Groups E2E — group-hierarchy Phase 4 PR 4-B.
 *
 * Drives `/groups` and `/groups/:id` against the live docker-compose dev
 * stack, through `GroupsHarness` (PR 4-A) exclusively — no ad-hoc selectors.
 *
 * Scope note: `tests/_harness/seed.ts` (PR 4-A's own prerequisite, owned by
 * `test-writer`) seeds one FLAT team per run — it has no option to seed a
 * parent/child group pair or a cascade-inherited membership. That leaves two
 * harness verbs this spec cannot exercise against real data:
 *   - `clickBreadcrumbSegment` / `clickDrilldownBreadcrumbSegment` on an
 *     actual ANCESTOR (the seeded team has none — this spec asserts the
 *     empty-ancestor case instead, via `expectBreadcrumb([])`).
 *   - `expectInheritedMemberRow` (needs a two-level hierarchy + cascade —
 *     this spec asserts `expectInheritedSectionEmpty()` instead, which is
 *     the correct behavior for a root team and equally real coverage).
 * A nested-group seed fixture is a `test-writer` follow-up, not something
 * this PR's scope (or its edit permissions) covers.
 *
 * The golden path this spec DOES cover end to end: detail loads directly by
 * id → breadcrumb is empty for a root group → members split into direct
 * (the seeded extra member) vs. inherited (empty) → the list mounts at root
 * → the seeded team appears as a row → search narrows to it by name →
 * opening its detail from the list lands back on the same group.
 *
 * Pre-requisites (auto-skip otherwise): docker-compose -f
 * docker-compose.dev.yml up -d, python3 on PATH.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { GroupsHarness } from "../_harness/GroupsHarness";
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

test.describe("groups", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("detail loads by id, members split direct/inherited, list search finds it and reopens it", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["groups-e2e"],
      extraMembers: 1,
    });
    if (seed === null) return;
    const extraMember = seed.extra_members![0];
    expect(extraMember.role).toBe("developer");

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const groups = new GroupsHarness(page);

    // Detail, reached directly by the seeded team's id (the flat seed fixture
    // gives us no name to search by yet — this page is where we learn it).
    await groups.gotoDetail(seed.team_id);
    await groups.expectDetailMounted();

    // Root group: no ancestors at all, not merely an ancestor we can't reach.
    await groups.expectBreadcrumb([]);

    const groupName = (
      await page.getByTestId("group-detail-name").textContent()
    )?.trim();
    expect(groupName).toBeTruthy();

    // Members: the seeded extra member is a DIRECT row; with no ancestors,
    // "inherited" is unconditionally empty (see module docstring).
    await groups.openMembersSection();
    await groups.expectDirectMemberRow(extraMember.email, "developer");
    await groups.expectInheritedSectionEmpty();

    // List: root drilldown shows the seeded team as a row.
    await groups.goto();
    await groups.expectGroupRow(groupName!);

    // Flat, whole-tree search narrows to it by name.
    await groups.search(groupName!);
    await groups.expectGroupRow(groupName!);

    // Opening detail from the list lands on the same group.
    await groups.openDetail(groupName!);
    await groups.expectDetailName(groupName!);
  });

  test("a nonexistent group id 404s as not-found (existence-hide)", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: ["groups-e2e-404"] });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const groups = new GroupsHarness(page);
    await groups.gotoDetail("00000000-0000-0000-0000-000000000000");
    await groups.expectNotFound();
  });
});
