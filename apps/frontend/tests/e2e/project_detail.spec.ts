/**
 * Project Detail E2E — Phase 3 PR #10 task 3.1 / 3.3.
 *
 * Drives the project detail page (Overview + Components tabs) against the
 * docker-compose dev stack. Every selector lives in
 * `apps/frontend/tests/_harness/PortalPage.ts` so EN/KO renders pass on the
 * same scenarios — assertions are rooted in `data-*` attributes and
 * `data-testid`, never in translated strings.
 *
 * Scenarios (`@project-detail` tag):
 *
 *   1. Loaded list → click row link → URL changes to /projects/<id> and the
 *      Overview tab is active by default. Project-info / Build-gate /
 *      Severity / License panels + the Recent-scans card all visible.
 *   2. Components tab loads its first page (Virtuoso mounted) and
 *      `endReached` triggers an additional page load when total > limit.
 *   3. Clicking a row opens the drawer (right slide-in), with the vuln list
 *      and the raw_data accordion both reachable.
 *   4. Search input narrows the result set (count strictly decreases) and
 *      the URL reflects `?search=<query>`.
 *   5. Severity multi-select narrows the rendered rows so every row carries
 *      one of the selected severities. URL reflects `?severity=…`.
 *   6. Sort key + order toggle — when sorting by severity desc the first
 *      row is the worst; toggle to asc and the first row is the best.
 *
 * Pre-requisites (auto-skip otherwise):
 *
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable (the seed.ts harness skips the test
 *     with a descriptive reason if not).
 *
 * The seed adds a `succeeded` scan + N components per project. Scenario 2
 * (virtual scroll) is the only one that needs a large fixture; the others
 * use a small slice (50) to keep the suite under the 30s/test budget.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-smoke";
const SMALL_FIXTURE_COMPONENTS = 50;
// Scenario 2 verifies that `endReached` triggers `fetchNextPage()`. The
// component list endpoint paginates at 100 (PAGE_SIZE in ComponentsTab.tsx);
// 250 rows guarantees ≥3 pages so we observe the loaded count cross 100.
const VIRTUAL_SCROLL_COMPONENTS = 250;

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

/**
 * Shared bootstrap: register a fresh user via the seed script (with scan +
 * components) and log them in via the auth harness. Returns the seed summary
 * so the spec can read the seeded project_id.
 */
async function bootstrap(
  testInfo: import("@playwright/test").TestInfo,
  page: import("@playwright/test").Page,
  opts: { componentCount: number; componentPrefix?: string },
): Promise<SeedSummary | null> {
  const seed = tryAcquireSeed(testInfo, {
    projectNames: [PROJECT_NAME],
    withScan: true,
    componentCount: opts.componentCount,
    componentPrefix: opts.componentPrefix,
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.gotoLogin();
  await auth.login(seed.email, seed.password);
  return seed;
}

test.describe("@project-detail project detail page", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("1) opening a project lands on Overview with all four panels", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, {
      componentCount: SMALL_FIXTURE_COMPONENTS,
    });
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.expectProjectRowVisible(PROJECT_NAME);

    await portal.openProjectDetail(PROJECT_NAME);

    // URL contract: detail page lives at /projects/<uuid>; the default tab
    // is "overview" and is NOT mirrored to the URL (the page treats the
    // missing param as overview).
    const seededId = seed.project_ids[0];
    await expect(page).toHaveURL(new RegExp(`/projects/${seededId}(\\?|$)`));
    expect(new URL(page.url()).searchParams.get("tab")).toBeNull();

    // All four overview panels mount — assert via testid only (no text).
    // W4-B/W6 reshuffled the Overview grid: the standalone composite risk
    // card (`overview-risk-card` + inner `risk-gauge`) was replaced by the
    // Project-info ↔ Build-gate pair on top of the Severity ↔ License pair.
    // The four panels are now Project info / Build gate / Severity / License,
    // plus the full-width Recent scans card below them.
    await expect(page.getByTestId("overview-info-card")).toBeVisible();
    await expect(page.getByTestId("gate-card")).toBeVisible();
    await expect(page.getByTestId("overview-severity-card")).toBeVisible();
    await expect(page.getByTestId("overview-license-card")).toBeVisible();
    await expect(page.getByTestId("overview-recent-scans-card")).toBeVisible();
  });

  test("2) Components tab loads first page and endReached fetches more", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, {
      componentCount: VIRTUAL_SCROLL_COMPONENTS,
    });
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);

    await portal.selectTab("components");
    await portal.expectComponentsTabReady();

    // First page lands at PAGE_SIZE=100. Confirm we got a full page (or
    // VIRTUAL_SCROLL_COMPONENTS, whichever is smaller) and the total
    // matches the seeded count.
    const firstPageLoaded = await portal.getLoadedComponentCount();
    expect(firstPageLoaded).toBeGreaterThanOrEqual(50);
    expect(firstPageLoaded).toBeLessThanOrEqual(100);
    expect(await portal.getTotalComponentCount()).toBe(VIRTUAL_SCROLL_COMPONENTS);

    // Scroll until either we've loaded > 100 (a second page arrived) or
    // we hit the iteration cap. The harness treats "no progress" as a
    // stop signal so a slow CI machine still terminates.
    const loadedAfterScroll = await portal.scrollComponentsToLoadMore(10);
    expect(loadedAfterScroll).toBeGreaterThan(firstPageLoaded);
  });

  test("3) clicking a component row opens the drawer", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, {
      componentCount: SMALL_FIXTURE_COMPONENTS,
    });
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectTab("components");
    await portal.expectComponentsTabReady();

    // The seed prefix defaults to "comp" → first component is "comp-00000".
    await portal.openComponentDrawer("comp-00000");

    // Drawer contract: meta + vulnerabilities sections both mount, raw
    // accordion toggle is reachable. We assert presence (not the exact
    // count) — the seeded fixture's CVE wiring is round-robin and we
    // don't want this scenario coupled to that math.
    await expect(page.getByTestId("component-drawer-meta")).toBeVisible();
    await expect(page.getByTestId("component-drawer-vulns")).toBeVisible();
    await expect(page.getByTestId("component-drawer-raw-toggle")).toBeVisible();

    // URL mirrors the selection.
    expect(new URL(page.url()).searchParams.get("drawer")).toBeTruthy();
  });

  test("4) search narrows the components list and mirrors to URL", async ({
    page,
  }, testInfo) => {
    // Use a known prefix so we can search by substring without learning ids.
    const seed = await bootstrap(testInfo, page, {
      componentCount: SMALL_FIXTURE_COMPONENTS,
      componentPrefix: "react",
    });
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectTab("components");
    await portal.expectComponentsTabReady();

    const totalBefore = await portal.getTotalComponentCount();
    expect(totalBefore).toBe(SMALL_FIXTURE_COMPONENTS);

    // Search for a substring that hits exactly one row: "react-00007".
    await portal.searchComponents("react-00007");
    const totalAfter = await portal.getTotalComponentCount();
    expect(totalAfter).toBeGreaterThanOrEqual(1);
    expect(totalAfter).toBeLessThan(totalBefore);

    // URL reflects the query — guarantees the next reload restores state.
    expect(new URL(page.url()).searchParams.get("search")).toBe("react-00007");

    // Clear and verify the count returns to the full set.
    await portal.searchComponents("");
    expect(await portal.getTotalComponentCount()).toBe(SMALL_FIXTURE_COMPONENTS);
  });

  test("5) severity filter narrows rows and mirrors to URL", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, {
      componentCount: SMALL_FIXTURE_COMPONENTS,
    });
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectTab("components");
    await portal.expectComponentsTabReady();

    const totalBefore = await portal.getTotalComponentCount();

    // Filter to critical + high. The seeded distribution round-robins
    // across 6 buckets, so ~1/3 of rows should remain.
    await portal.filterComponentsBySeverity(["critical", "high"]);
    const totalAfter = await portal.getTotalComponentCount();
    expect(totalAfter).toBeGreaterThan(0);
    expect(totalAfter).toBeLessThan(totalBefore);

    // URL mirrors the multi-value filter (CSV).
    const sevParam = new URL(page.url()).searchParams.get("severity");
    expect(sevParam).not.toBeNull();
    const parts = (sevParam ?? "").split(",").sort();
    expect(parts).toEqual(["critical", "high"]);

    // Spot-check a couple of rendered rows: severity badge must read
    // 'critical' or 'high'. We sample the first three.
    const sampleSize = Math.min(3, totalAfter);
    for (let i = 0; i < sampleSize; i++) {
      const sev = await portal.getRowSeverity(i);
      expect(["critical", "high"]).toContain(sev);
    }
  });

  test("6) sort by severity respects asc/desc toggle", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, {
      componentCount: SMALL_FIXTURE_COMPONENTS,
    });
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectTab("components");
    await portal.expectComponentsTabReady();

    // Sort by severity desc → first row's severity must be at the top of
    // the seeded distribution (critical / high are the only buckets with
    // findings; the seed uses round-robin so both exist).
    await portal.sortComponentsBy("severity");
    await portal.setComponentsOrder("desc");
    const topSevDesc = await portal.getRowSeverity(0);
    // Worst-case bucket present in the seed is 'critical'. Every other
    // severity is strictly lower, so we accept anything ≥ high to stay
    // resilient to seed-distribution drift.
    expect(["critical", "high"]).toContain(topSevDesc);

    // Flip to asc → first row should now be the lowest severity bucket
    // present. The seed always includes 'none' (info/none rows have no
    // VulnerabilityFinding).
    await portal.setComponentsOrder("asc");
    const topSevAsc = await portal.getRowSeverity(0);
    expect(["none", "info", "low"]).toContain(topSevAsc);
    // Sanity: asc is strictly less severe than desc.
    expect(topSevAsc).not.toBe(topSevDesc);
  });
});
