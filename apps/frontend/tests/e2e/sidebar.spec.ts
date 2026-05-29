/**
 * Sidebar E2E — collapsible rail + responsive drawer.
 *
 * Covers the two behaviours added for the "can the user hide the sidebar /
 * does it adapt to width?" gap:
 *   1. Desktop (≥lg): the user collapses the 224 px sidebar to a 64 px icon
 *      rail; the choice persists across a reload (localStorage via uiStore).
 *   2. Narrow viewport (<lg): the fixed sidebar is replaced by a header
 *      hamburger that opens an overlay drawer, which closes on navigate.
 *
 * Pre-requisites (auto-skip otherwise), identical to the other authenticated
 * specs:
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 *
 * Selectors live in `tests/_harness/PortalPage.ts` + `tests/_harness/auth.ts`
 * — every assertion is rooted in `data-testid`, never a translated string.
 * Authored + typechecked here; runs in the nightly e2e workflow.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "sidebar-smoke";

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

test.describe("@sidebar collapse rail + responsive drawer", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("desktop: collapsing the sidebar persists across reload", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: [PROJECT_NAME] });
    if (seed === null) return;

    await page.setViewportSize({ width: 1280, height: 800 });

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    await portal.expectMounted();

    // Starts expanded.
    await portal.expectSidebarExpanded();

    // Collapse to the icon rail.
    await portal.toggleSidebarCollapse();
    await portal.expectSidebarCollapsed();

    // Persists across a full reload (re-bootstrap from the refresh cookie).
    await page.reload();
    await portal.expectMounted();
    await portal.expectSidebarCollapsed();

    // And the user can expand it again.
    await portal.toggleSidebarCollapse();
    await portal.expectSidebarExpanded();
  });

  test("narrow viewport: hamburger opens a drawer that closes on navigate", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: [PROJECT_NAME] });
    if (seed === null) return;

    await page.setViewportSize({ width: 800, height: 700 });

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    // Below `lg` the fixed sidebar is CSS-hidden and the hamburger appears.
    await expect(page.getByTestId("sidebar-mobile-trigger")).toBeVisible();
    await expect(page.getByTestId("app-sidebar")).toBeHidden();

    const portal = new PortalPage(page);
    await portal.openMobileNav();

    // The drawer carries the full nav; clicking an item navigates and closes.
    await portal.mobileNavDrawer().getByTestId("nav-projects").click();
    await expect(portal.mobileNavDrawer()).toBeHidden();
    await expect(page).toHaveURL(/\/projects$/);
  });
});
