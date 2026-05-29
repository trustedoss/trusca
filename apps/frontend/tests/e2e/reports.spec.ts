/**
 * Reports tab E2E — W3 #32.
 *
 * Drives the project detail Reports tab against the docker-compose dev stack.
 * Single happy-path scenario (the unit tests in
 * `tests/unit/features/projects/ReportsTab.test.tsx` cover filter / pagination
 * / error / empty branches without a backend):
 *
 *   S1 — Tab entry: all four generate cards visible, history table (or empty
 *        state) settles. Clicking the NOTICE card deeplinks to the Obligations
 *        tab + the URL switches to ``?tab=obligations``. The Reports tab
 *        survives a hard reload at ``?tab=reports``.
 *
 * Selectors live in `apps/frontend/tests/_harness/PortalPage.ts`. Every
 * assertion uses `data-testid` so the spec is locale-agnostic.
 *
 * Pre-requisites (auto-skip otherwise):
 *
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 reachable from host PATH (the seed.ts harness validates this)
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-reports";

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
    componentCount: 4,
    componentPrefix: "rpt",
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.gotoLogin();
  await auth.login(seed.email, seed.password);
  return seed;
}

test.describe("@reports project reports tab", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("S1) Reports tab renders the four generate cards and the NOTICE card downloads directly", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectReportsTab();

    // All four cards mount unconditionally — they are navigation entry
    // points, not data-dependent.
    await expect(page.getByTestId("reports-card-notice")).toBeVisible();
    await expect(page.getByTestId("reports-card-sbom")).toBeVisible();
    await expect(page.getByTestId("reports-card-vuln-pdf")).toBeVisible();
    await expect(page.getByTestId("reports-card-vex")).toBeVisible();

    // History either resolved to rows OR to the empty card — both are valid
    // settled states for a fresh seed (no downloads have happened yet for
    // this project unless other tests ran earlier in the suite).
    const table = page.getByTestId("reports-history-table");
    const empty = page.getByTestId("reports-history-empty");
    await expect(table.or(empty)).toBeVisible();

    // URL mirrors `?tab=reports` (the tab-selection contract).
    expect(new URL(page.url()).searchParams.get("tab")).toBe("reports");

    // The Reports tab survives a hard reload.
    await page.reload();
    await portal.expectReportsTabReady();
    expect(new URL(page.url()).searchParams.get("tab")).toBe("reports");

    // NOTICE downloads directly from the card (the unified Compliance tab is a
    // read-only grid, so the old deep-link was a dead end). The format picker
    // and download button are present, and a click yields a NOTICE-*.txt file.
    await expect(
      page.getByTestId("reports-card-notice-format"),
    ).toBeVisible();
    const download = await portal.downloadNoticeFromReports("text");
    expect(download.suggestedFilename()).toMatch(/^NOTICE-.*\.txt$/);
    // Staying on the Reports tab — no navigation happened.
    expect(new URL(page.url()).searchParams.get("tab")).toBe("reports");
  });
});
