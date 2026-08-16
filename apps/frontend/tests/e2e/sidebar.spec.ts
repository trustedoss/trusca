/**
 * Sidebar E2E — collapsible rail + responsive drawer.
 *
 * Covers the two behaviours added for the "can the user hide the sidebar /
 * does it adapt to width?" gap:
 *   1. Desktop (≥lg): the user collapses the 256 px sidebar to a 64 px icon
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

  test("the skip link takes the first Tab and moves focus to the content", async ({
    page,
  }, testInfo) => {
    // jsdom will happily report that an anchor with href="#main-content" was
    // focused; whether the browser then moves focus to the target depends on
    // the target being focusable, which is exactly the part that regresses.
    const seed = tryAcquireSeed(testInfo, { projectNames: [PROJECT_NAME] });
    if (seed === null) return;

    await page.setViewportSize({ width: 1280, height: 800 });

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    await portal.expectMounted();

    await page.locator("body").click({ position: { x: 2, y: 2 } });
    await page.keyboard.press("Tab");

    const skip = page.getByTestId("skip-to-content");
    await expect(skip).toBeFocused();
    // sr-only until focused, then it has to actually be on screen: a skip
    // link nobody can see is one nobody knows they hit.
    await expect(skip).toBeVisible();

    await page.keyboard.press("Enter");
    await expect(page.getByTestId("app-main")).toBeFocused();
  });

  test("the account menu holds sign-out, theme, language and the docs link", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: [PROJECT_NAME] });
    if (seed === null) return;

    // 390 px: the width at which theme and language used to disappear with no
    // replacement, which is the reason they moved into this menu.
    await page.setViewportSize({ width: 390, height: 844 });

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    await portal.expectMounted();
    await portal.openProfileMenu();

    for (const testId of [
      "header-profile-link",
      "header-docs-link",
      "header-shortcuts-link",
      "theme-toggle",
      "language-toggle",
      "logout-button",
    ]) {
      await expect(page.getByTestId(testId)).toBeVisible();
    }

    // The docs link is the one that leaves the app; a target of _blank with
    // no `noopener` would hand the opener window to whatever it points at.
    const docs = page.getByTestId("header-docs-link");
    await expect(docs).toHaveAttribute("target", "_blank");
    await expect(docs).toHaveAttribute("rel", /noopener/);
  });

  test("? opens the shortcut sheet, but not while the user is typing", async ({
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

    await page.keyboard.press("?");
    await expect(page.getByTestId("shortcut-help-dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("shortcut-help-dialog")).toBeHidden();

    // Inside a text field the character has to reach the field instead. The
    // command menu's own input is the one every user meets first.
    await page.getByTestId("command-menu-trigger").click();
    const input = page.getByTestId("command-menu-input");
    await input.fill("");
    await page.keyboard.type("who?");
    await expect(input).toHaveValue("who?");
    await expect(page.getByTestId("shortcut-help-dialog")).toBeHidden();
  });
});
