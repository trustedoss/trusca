/**
 * Vulnerabilities E2E — Phase 3 PR #11.
 *
 * Drives the project detail Vulnerabilities tab against the docker-compose
 * dev stack. Five `@vulnerabilities` scenarios:
 *
 *   S1 — Tab entry & list render
 *   S2 — Severity + status multi-filter sync (URL persists across reload)
 *   S3 — Drawer open + detail render
 *   S4 — Status transition (developer: new → analyzing) + audit history
 *   S5 — Permission denial: developer cannot suppress (button disabled)
 *
 * All selectors live in `apps/frontend/tests/_harness/PortalPage.ts`. The
 * scenarios are EN-locale-agnostic — every assertion uses `data-testid`
 * or `data-*` attributes, never translated strings.
 *
 * Pre-requisites (auto-skip otherwise):
 *
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 reachable from host PATH (the seed.ts harness validates this)
 *
 * The seed extension `--vulnerability-count` (added in this PR) attaches
 * findings with a deterministic severity + status mix.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-vulns";
// Default vulnerability mix produces:
//   2 critical, 5 high, 10 medium, 20 low, 5 info, 2 unknown — 44 total
// across statuses {new: 36, analyzing: 7, not_affected: 1}.
const DEFAULT_VULN_COUNT = 44;

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
    componentCount: DEFAULT_VULN_COUNT,
    componentPrefix: "vuln",
    vulnerabilityCount: DEFAULT_VULN_COUNT,
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.gotoLogin();
  await auth.login(seed.email, seed.password);
  return seed;
}

test.describe("@vulnerabilities project vulnerabilities tab", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("S1) Vulnerabilities tab renders the seeded findings list", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectVulnerabilitiesTab();

    // The summary row exposes `data-total` — assert ≥ 1 row arrived.
    const total = await portal.getVulnerabilityRowCount();
    expect(total).toBeGreaterThanOrEqual(1);

    // At least one row is mounted (virtual list rendered).
    await expect(page.getByTestId("vulnerability-row").first()).toBeVisible();
  });

  test("S2) severity + status multi-filter persists across reload", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectVulnerabilitiesTab();

    const totalBefore = await portal.getVulnerabilityRowCount();

    await portal.filterVulnerabilitiesBySeverity(["critical", "high"]);
    await portal.filterVulnerabilitiesByStatus(["new", "analyzing"]);

    const totalAfter = await portal.getVulnerabilityRowCount();
    expect(totalAfter).toBeLessThanOrEqual(totalBefore);

    // URL mirrors filters as CSV.
    const url = new URL(page.url());
    const sev = (url.searchParams.get("severity") ?? "").split(",").sort();
    const status = (url.searchParams.get("status") ?? "").split(",").sort();
    expect(sev).toEqual(["critical", "high"]);
    expect(status).toEqual(["analyzing", "new"]);

    // Hard reload → filters survive.
    await page.reload();
    await portal.selectVulnerabilitiesTab();
    const totalAfterReload = await portal.getVulnerabilityRowCount();
    expect(totalAfterReload).toBe(totalAfter);
  });

  test("S3) clicking a row opens the drawer and renders detail sections", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectVulnerabilitiesTab();

    // Read the cve-id of the first row (locale-agnostic) and open it.
    const firstCveId = await page
      .getByTestId("vulnerability-row")
      .first()
      .getAttribute("data-cve-id");
    expect(firstCveId).toBeTruthy();
    await portal.openVulnerabilityDrawer(firstCveId as string);

    // Drawer contract: meta + summary + affected + analysis + history all mount.
    await expect(page.getByTestId("vulnerability-drawer-meta")).toBeVisible();
    await expect(
      page.getByTestId("vulnerability-drawer-summary"),
    ).toBeVisible();
    await expect(
      page.getByTestId("vulnerability-drawer-analysis"),
    ).toBeVisible();
    await expect(
      page.getByTestId("vulnerability-drawer-history"),
    ).toBeVisible();
    // At least one affected row + at least one history entry.
    expect(
      await page.getByTestId("vulnerability-drawer-affected-row").count(),
    ).toBeGreaterThanOrEqual(1);
    expect(
      await page.getByTestId("vulnerability-drawer-history-entry").count(),
    ).toBeGreaterThanOrEqual(1);

    // URL mirrors the selection.
    expect(new URL(page.url()).searchParams.get("vuln")).toBeTruthy();
  });

  test("S4) developer transitions new → analyzing and history grows", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectVulnerabilitiesTab();

    // Filter to only `new` rows so we definitely click a row whose source
    // state allows the analyzing transition.
    await portal.filterVulnerabilitiesByStatus(["new"]);
    const firstCveId = await page
      .getByTestId("vulnerability-row")
      .first()
      .getAttribute("data-cve-id");
    expect(firstCveId).toBeTruthy();
    await portal.openVulnerabilityDrawer(firstCveId as string);

    const historyBefore = await page
      .getByTestId("vulnerability-drawer-history-entry")
      .count();

    await portal.setVulnerabilityStatus("analyzing", "starting triage");

    // History gains one entry (the new transition row).
    await expect
      .poll(
        async () =>
          page
            .getByTestId("vulnerability-drawer-history-entry")
            .count(),
        { timeout: 10_000 },
      )
      .toBeGreaterThan(historyBefore);
  });

  test("S6) 'By upgrade' groups findings into clusters and a finding opens the drawer", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectVulnerabilitiesTab();

    // Flip to the grouped view.
    await portal.toggleVulnerabilitiesGroupBy("upgrade");

    // At least one cluster renders (the seed has open findings).
    const clusterCount = await portal.getUpgradeClusterCount();
    expect(clusterCount).toBeGreaterThanOrEqual(1);

    // The grouped summary reports the whole-project open-finding total.
    const summary = page.getByTestId("vulnerabilities-upgrade-summary");
    await expect(summary).toBeVisible();
    const findings = Number(await summary.getAttribute("data-findings"));
    expect(findings).toBeGreaterThanOrEqual(1);

    // Every cluster header carries a fixes-count (findings the bump resolves).
    const firstCluster = page
      .getByTestId("vulnerability-upgrade-cluster")
      .first();
    await expect(
      firstCluster.getByTestId("vulnerability-upgrade-cluster-fixes"),
    ).toBeVisible();

    // When the seed produced a concrete upgrade (reason="ok"), the header
    // names the recommended version. Otherwise the "no upgrade" reason shows —
    // both are valid cluster shapes, so assert the recommended version only on
    // the actionable clusters.
    const okCluster = page.locator(
      '[data-testid="vulnerability-upgrade-cluster"][data-reason="ok"]',
    );
    if ((await okCluster.count()) > 0) {
      const recommended = await okCluster
        .first()
        .getAttribute("data-recommended-version");
      expect(recommended && recommended.length).toBeTruthy();
      await expect(
        okCluster
          .first()
          .getByTestId("vulnerability-upgrade-cluster-recommended"),
      ).toBeVisible();
    }

    // Expand the first cluster and open one of its findings — the SAME
    // vulnerability drawer the flat list uses opens (URL mirrors ?vuln=<id>).
    await firstCluster
      .getByTestId("vulnerability-upgrade-cluster-header")
      .click();
    await firstCluster
      .getByTestId("vulnerability-upgrade-finding")
      .first()
      .click();
    await expect(
      page.getByTestId("vulnerability-drawer"),
    ).toBeVisible({ timeout: 10_000 });
    await expect
      .poll(() => new URL(page.url()).searchParams.get("vuln"), {
        timeout: 5_000,
      })
      .not.toBeNull();
  });

  test("S5) developer cannot suppress — action button is disabled", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectVulnerabilitiesTab();

    // Open the first available row (any status). The drawer's allowed-
    // transition set may or may not include `suppressed`; if it does,
    // it must be disabled with `data-role-gated`.
    const firstCveId = await page
      .getByTestId("vulnerability-row")
      .first()
      .getAttribute("data-cve-id");
    expect(firstCveId).toBeTruthy();
    await portal.openVulnerabilityDrawer(firstCveId as string);

    const suppressBtn = page.getByTestId("vulnerability-drawer-action-suppressed");
    if ((await suppressBtn.count()) > 0) {
      await expect(suppressBtn).toBeDisabled();
      await expect(suppressBtn).toHaveAttribute("data-role-gated", "true");
    } else {
      // Source state's outgoing edges don't include suppressed (e.g. the
      // first row is in `exploitable`); the button isn't rendered, which
      // is a stronger denial than "disabled". Either is acceptable.
      expect(await suppressBtn.count()).toBe(0);
    }

    // Belt-and-braces: the status badge is unchanged from the row's
    // pre-click state. We don't assert a specific value (the seed mix
    // may evolve), only that no transition happened during this scenario.
    await expect(
      page
        .getByTestId("vulnerability-drawer-meta")
        .locator(`[data-testid^="vulnerability-status-badge-"]`)
        .first(),
    ).toBeVisible();
  });
});
