/**
 * Not-found surfaces E2E (A4).
 *
 * Three addresses that do not resolve, and what the product says about each:
 *
 *   1. A path that matches no route. Until A4 this redirected to /login, so a
 *      signed-in user who mistyped a URL was sent to a sign-in form and then
 *      bounced back to the dashboard, never told the address was wrong.
 *   2. A finding id that does not exist. The page rendered a red banner and
 *      nothing else: no reason, no way on except the browser's Back button.
 *   3. A scan id that does not exist.
 *
 * Each one should now name what happened and offer a way back, and the way
 * back should work.
 *
 * Pre-requisites (auto-skip otherwise), identical to the other authenticated
 * specs:
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 *
 * Tagged `@not-found` so it can be run alone while it evolves.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "not-found-smoke";

/** A syntactically valid id that the backend will not know. */
const ABSENT_ID = "00000000-0000-4000-8000-000000000000";

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

test.describe("@not-found addresses that do not resolve", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("a mistyped path lands on the 404 screen, which leads back to the dashboard", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: [PROJECT_NAME] });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
    await portal.expectMounted();

    await portal.goto("/prjects");

    // The screen exists, names the address, and is not the login form.
    await expect(page.getByTestId("not-found-page")).toBeVisible();
    await expect(page.getByTestId("not-found-page-path")).toContainText(
      "/prjects",
    );
    expect(page.url()).toContain("/prjects");

    // The shell is still there: a typo does not cost the user their
    // navigation.
    await portal.expectMounted();

    await page.getByTestId("not-found-page-home").click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByTestId("not-found-page")).toHaveCount(0);
  });

  test("an unknown finding id explains itself and offers the list", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: [PROJECT_NAME] });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
    await portal.expectMounted();

    await portal.goto(
      `/projects/${ABSENT_ID}/vulnerabilities/${ABSENT_ID}`,
    );

    const surface = page.getByTestId("vulnerability-detail-page-error");
    await expect(surface).toBeVisible();
    // The body must not mount behind the failure.
    await expect(page.getByTestId("vulnerability-drawer-meta")).toHaveCount(0);

    await page.getByTestId("vulnerability-detail-page-error-back").click();
    await expect(page).toHaveURL(/\/projects\//);
  });

  test("an unknown scan id explains itself and offers the scan list", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: [PROJECT_NAME] });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
    await portal.expectMounted();

    await portal.goto(`/scans/${ABSENT_ID}`);

    await expect(page.getByTestId("scan-detail-page-error")).toBeVisible();

    await page.getByTestId("scan-detail-page-error-back").click();
    await expect(page).toHaveURL(/\/scans$/);
  });
});
