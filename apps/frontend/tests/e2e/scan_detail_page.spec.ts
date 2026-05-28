/**
 * Scan detail page E2E — scan-detail-page-fe-v2.
 *
 * Covers the new dedicated `/scans/:scanId` page that replaces the cramped
 * right-drawer log view:
 *
 *   1. Trigger a scan from the project list → drawer opens with the summary.
 *   2. Drawer shows the "Open full log →" link to the dedicated page.
 *   3. Clicking the link navigates to `/scans/<id>` and the large log panel
 *      + stage filter chips render.
 *   4. The Download log button fires a browser download whose suggested
 *      filename matches `scan-<id>.log` (the backend Content-Disposition).
 *
 * Pre-requisites mirror `scan_flow.spec.ts` — docker-compose dev up with the
 * worker in mock-backend mode so the seeded scan publishes progress + log
 * frames in seconds.
 *
 * Tagged `@scan-detail-page` so CI can run this scenario file independently
 * of the broader `@scan-flow` suite while they evolve in parallel.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

function tryAcquireSeed(
  test: import("@playwright/test").TestInfo,
  opts: Parameters<typeof seedE2eUser>[0],
): SeedSummary | null {
  try {
    return seedE2eUser(opts);
  } catch (err) {
    test.skip(
      true,
      `seed precondition failed — bring docker-compose dev up + ensure python3 is on PATH: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
    return null;
  }
}

test.describe("@scan-detail-page dedicated /scans/:scanId surface", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("opens the full log page from the drawer link, renders panel + filters, and downloads scan-<id>.log", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, { projectNames: ["alpha"] });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);

    // --- Setup: log in + open the project list.
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
    await portal.gotoProjects();
    await portal.expectProjectRowVisible("alpha");

    // --- Trigger a scan; the drawer mounts with the summary + the new
    // "Open full log →" link to the dedicated page. The dedicated page
    // replaces the previous inline log surface (hideInlineLog flag).
    await portal.clickTriggerScan("alpha");
    await portal.expectScanProgress();
    await expect(
      page.getByTestId("scan-drawer-open-full-log"),
    ).toBeVisible({ timeout: 10_000 });

    // --- Click "Open full log →" — URL switches to /scans/<id>.
    await portal.openFullLogFromDrawer();

    // --- The dedicated page surfaces:
    //   • the page header (with short id + status badge),
    //   • the large log panel (data-testid="scan-detail-page-log"),
    //   • the stage filter chip row,
    //   • the download button.
    await expect(page.getByTestId("scan-detail-page")).toBeVisible();
    await expect(page.getByTestId("scan-detail-page-title")).toBeVisible();
    await expect(page.getByTestId("scan-detail-page-log")).toBeVisible();
    await expect(page.getByTestId("scan-detail-page-filters")).toBeVisible();
    await expect(
      page.getByTestId("scan-detail-page-filter-cdxgen"),
    ).toBeVisible();
    await expect(
      page.getByTestId("scan-detail-page-filter-errors"),
    ).toBeVisible();
    await expect(page.getByTestId("scan-detail-page-download")).toBeVisible();

    // --- The download button is gated (disabled while queued + no log
    // lines have arrived). With the mock backend the scan publishes log
    // frames in seconds; wait for the button to become enabled before
    // clicking. `expect(...).toBeEnabled` auto-retries — locale-agnostic.
    await expect(
      page.getByTestId("scan-detail-page-download"),
    ).toBeEnabled({ timeout: 30_000 });

    // --- Click Download → assert a download event with the canonical
    // filename matching the backend Content-Disposition contract.
    const scanIdMatch = new URL(page.url()).pathname.match(/\/scans\/([^/]+)$/);
    expect(scanIdMatch, "URL should be /scans/<id>").not.toBeNull();
    const scanId = scanIdMatch![1];

    const { filename } = await portal.downloadScanLog();
    expect(filename).toBe(`scan-${scanId}.log`);
  });
});
