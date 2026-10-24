/**
 * Signing out and signing in as someone else in the same tab.
 *
 * The page is never reloaded between the two sessions: sign-out goes through
 * the account menu and sign-in through the form, so whatever the app kept in
 * memory for the first user is still there unless sign-out dropped it. The
 * second user must see their own project list, not the first user's.
 *
 * The second scenario uses two tabs on one session: signing out in one must
 * sign the other out as well, since both share the refresh cookie.
 *
 * Pre-requisites (auto-skip otherwise), as the other authenticated specs:
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 *
 * Tagged `@user-switch`.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { MfaHarness } from "../_harness/MfaHarness";
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
      `seed precondition failed: bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

test.describe("@user-switch a second user in the same tab", () => {
  test.beforeEach(async ({ page }) => {
    await new AuthHarness(page).clearAuthState();
  });

  test("sees only their own projects after the first user signs out", async ({
    page,
  }, testInfo) => {
    const tag = Date.now().toString(36);
    const first = tryAcquireSeed(testInfo, {
      projectNames: [`switch-first-${tag}`],
    });
    if (first === null) return;
    const second = tryAcquireSeed(testInfo, {
      projectNames: [`switch-second-${tag}`],
    });
    if (second === null) return;

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    const session = new MfaHarness(page);

    // First user signs in and the project list is loaded into memory.
    await auth.gotoLogin();
    await auth.login(first.email, first.password);
    await portal.openProjectsFromSidebar();
    await portal.expectProjectRowVisible(first.project_names[0]);

    // Sign out and in again without reloading the page.
    await session.signOut();
    await auth.login(second.email, second.password);
    await portal.openProjectsFromSidebar();

    // The list the second user lands on is theirs alone.
    await portal.expectProjectRowVisible(second.project_names[0]);
    await portal.expectVisibleProjectCount(second.project_names.length);
  });

  test("signing out in one tab signs out the other", async ({
    page,
    context,
  }, testInfo) => {
    const tag = Date.now().toString(36);
    const seed = tryAcquireSeed(testInfo, {
      projectNames: [`switch-tabs-${tag}`],
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
    const portal = new PortalPage(page);
    await portal.openProjectsFromSidebar();
    await portal.expectProjectRowVisible(seed.project_names[0]);

    // A second tab picks up the same session from the shared cookie.
    const other = await context.newPage();
    const otherPortal = new PortalPage(other);
    await otherPortal.gotoProjects();
    await otherPortal.expectProjectRowVisible(seed.project_names[0]);

    // The first tab could also reach the sign-in screen on its own: a 401,
    // a refresh against the revoked cookie, then the expiry redirect. Count
    // its refresh attempts so the test proves the message did it instead.
    const refreshes: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/auth/refresh")) refreshes.push(request.url());
    });

    await new MfaHarness(other).signOut();

    // The first tab was not touched, yet it is back at the sign-in screen.
    await expect(page).toHaveURL(/\/login/);
    expect(refreshes).toEqual([]);
  });
});
