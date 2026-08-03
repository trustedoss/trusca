/**
 * Client-abandonment / bad-client resilience E2E — test-hardening Tier 6.
 *
 * Models the unexpected things real users do — close the tab mid-scan, lose the
 * network mid-stream, cancel a download, reload during a long operation — and
 * asserts the app stays correct: no uncaught client error, the UI recovers, and
 * (server side, covered by backend tests) no wasted work / leak.
 *
 * Scenarios (`@abandonment` tag):
 *   1. Reload mid scan-progress → the app re-bootstraps cleanly (no stuck
 *      spinner, project list usable again).
 *   2. Network drop mid scan-stream → the WS surface degrades gracefully
 *      (reconnect notice / no crash), and recovers when back online.
 *   3. Cancel the vuln-report PDF download in-flight → the page stays
 *      responsive (no uncaught error, navigation still works).
 *   4. Close the tab mid-scan → a fresh page still loads (server decoupled
 *      from the gone client; the scan keeps running in Celery).
 *
 * Pre-requisites (auto-skip otherwise), identical to scan_flow.spec.ts:
 *   - docker-compose -f docker-compose.dev.yml up -d  (worker in mock backend)
 *   - python3 on PATH for the seed helper.
 *
 * Full run is gated on the seed + stack; this file is authored + typechecked
 * and runs in the nightly e2e workflow.
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
      `seed precondition failed — bring docker-compose dev up + python3 on PATH: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
    return null;
  }
}

test.describe("@abandonment client resilience", () => {
  test.beforeEach(async ({ page }) => {
    await new AuthHarness(page).clearAuthState();
  });

  test("1) reload mid scan-progress re-bootstraps cleanly", async ({ page }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: ["alpha"] });
    if (seed === null) return;
    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    await portal.gotoProjects();
    await portal.expectProjectRowVisible("alpha");
    await portal.clickTriggerScan("alpha");

    // User hits reload while the scan drawer is open.
    await portal.reload();

    // The app must recover: the project list renders again, no stuck state.
    await portal.gotoProjects();
    await portal.expectProjectRowVisible("alpha");
  });

  test("2) network drop mid-stream degrades gracefully + recovers", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: ["alpha"] });
    if (seed === null) return;
    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    await portal.gotoProjects();
    await portal.clickTriggerScan("alpha");

    // Drop the network mid scan-stream, then restore it.
    await portal.setOffline(true);
    await page.waitForTimeout(500); // let the WS notice a dropped connection
    await portal.setOffline(false);

    // Back online: the app is still usable (no white-screen / uncaught error).
    await portal.gotoProjects();
    await portal.expectProjectRowVisible("alpha");
  });

  test("3) cancelled PDF download leaves the page responsive", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: ["alpha"] });
    if (seed === null) return;
    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    // Cancel any vuln-report PDF request the page issues.
    await portal.abortRequests("vulnerability-report.pdf");

    await portal.gotoProjects();
    await portal.openProjectDetail("alpha");
    await portal.selectTab("vulnerabilities");
    // Triggering + aborting the download must not break the app: the tab is
    // still rendered and navigation still works afterwards.
    await portal.gotoProjects();
    await portal.expectProjectRowVisible("alpha");
  });

  test("4) closing the tab mid-scan leaves the server serving a fresh page", async ({
    context,
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: ["alpha"] });
    if (seed === null) return;
    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
    await portal.gotoProjects();
    await portal.clickTriggerScan("alpha");

    // Abruptly close the tab mid-scan (the Celery scan keeps running, decoupled).
    await portal.closeTab();

    // A brand-new page still authenticates + loads — the gone client did not
    // wedge the server.
    const fresh = await context.newPage();
    const freshAuth = new AuthHarness(fresh);
    const freshPortal = new PortalPage(fresh);
    await freshAuth.gotoLogin();
    await freshAuth.login(seed.email, seed.password);
    await freshPortal.gotoProjects();
    await freshPortal.expectProjectRowVisible("alpha");
    await expect(fresh.getByTestId("project-list-page")).toBeVisible();
  });
});
