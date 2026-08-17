/**
 * Vulnerabilities paging + drawer history, A2.
 *
 * Two defects this pins, both of which made the table read as broken rather
 * than as limited:
 *
 *   P1: The 101st row was unreachable. The page size is 100, the tab wrote a
 *        `?page=` parameter, and nothing ever incremented it. A project with
 *        more than 100 findings simply hid the rest, with no control to page
 *        and no notice that the list was cut.
 *   P2: Opening a finding replaced the history entry, so Back left the tab
 *        entirely: discarding filters and scroll position, instead of
 *        closing the panel the user had just opened by clicking a row.
 *
 * Seeded separately from `vulnerabilities.spec.ts` because it needs more than
 * one page of findings, and a 44-finding project cannot show a paging defect.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-vulns-paging";
/** One full page (100) plus enough to prove a second page arrives. */
const VULN_COUNT = 130;

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

async function bootstrap(
  testInfo: import("@playwright/test").TestInfo,
  page: import("@playwright/test").Page,
): Promise<SeedSummary | null> {
  const seed = tryAcquireSeed(testInfo, {
    projectNames: [PROJECT_NAME],
    withScan: true,
    componentCount: VULN_COUNT,
    // Unique per spec: the seed keys components by this prefix, and reusing
    // another spec's prefix collides on a shared stack.
    componentPrefix: "vulnpage",
    vulnerabilityCount: VULN_COUNT,
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.gotoLogin();
  await auth.login(seed.email, seed.password);
  return seed;
}

test.describe("@vulnerabilities paging and drawer history", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("P1) scrolling past the first page loads the rest", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectVulnerabilitiesTab();
    await portal.expectVulnerabilitiesTabReady();

    const total = await portal.getVulnerabilityRowCount();
    expect(total).toBeGreaterThan(100);

    const firstPage = await portal.getVulnerabilityLoadedCount();
    expect(firstPage).toBe(100);

    const afterScroll = await portal.scrollVulnerabilitiesToEnd(firstPage);
    expect(afterScroll).toBeGreaterThan(100);
  });

  test("P3) the rendered window is the same on two loads of one dataset", async ({
    page,
  }, testInfo) => {
    // #114: two captures of one commit differed in a 12 px strip at the
    // bottom of this table, showing different package identifiers. Either the
    // virtual window depended on when the shutter opened, or the query's
    // ordering was not total and pagination could repeat or skip a row. This
    // tells the two apart in one assertion: same fixture, same filters, two
    // independent loads. Different ids mean the order moved; the same ids in
    // a different window mean the extent moved. Identical means neither.
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectVulnerabilitiesTab();
    await portal.expectVulnerabilityWindowSettled();
    const first = await portal.getRenderedVulnerabilityWindow();
    expect(first.length).toBeGreaterThan(0);
    expect(first).not.toContain("");

    await page.reload();
    await portal.expectVulnerabilityWindowSettled();
    const second = await portal.getRenderedVulnerabilityWindow();

    expect(second).toEqual(first);
  });

  test("P2) Back closes the drawer instead of leaving the tab", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectVulnerabilitiesTab();
    await portal.expectVulnerabilitiesTabReady();

    await page.getByTestId("vulnerability-row").first().click();
    await expect(page.getByTestId("vulnerability-drawer")).toBeVisible();
    expect(page.url()).toContain("vuln=");

    await page.goBack();

    // The panel closes and the table is still there, on the same tab.
    await expect(page.getByTestId("vulnerability-drawer")).toBeHidden();
    expect(page.url()).not.toContain("vuln=");
    await expect(page.getByTestId("vulnerabilities-virtual")).toBeVisible();
  });
});
