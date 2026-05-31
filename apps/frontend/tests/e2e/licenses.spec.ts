/**
 * Licenses E2E — Phase 3 PR #12 (re-targeted to the W9-#58 unified
 * Compliance grid).
 *
 * The standalone Licenses tab was absorbed into a single read-only
 * Compliance grid (`?tab=compliance`). Rows are now `compliance-row`
 * elements carrying `data-category` / `data-spdx-id` / `data-finding-id`;
 * the per-license drawer (LicenseDrawer) is reused verbatim. The harness
 * `selectLicensesTab` / `filterLicensesByCategory` / `openLicenseDrawer`
 * verbs were updated to drive the grid, so the spec keeps its original
 * intent (rows + counts, category filter, drawer meta + affected, cross-link
 * to Components).
 *
 * Four `@licenses` scenarios:
 *
 *   S1 — Tab entry: grid rows render and each carries a valid category
 *   S2 — Category multi-filter sync (URL persists, narrows results)
 *   S3 — Drawer open: meta + affected components render
 *   S4 — Cross-link from drawer pivots to the Components tab drawer
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
 * The seed `--component-count` produces a round-robin license mix across
 * the four categories (`_LICENSE_CATEGORY_CYCLE` in seed_e2e_user.py), so
 * the seeded project always has ≥ 1 row per category. Each license uses
 * an SPDX id of the form `E2E-<CAT>-<suffix>`; we resolve the actual
 * SPDX id at runtime so the spec stays decoupled from suffix randomness.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-licenses";
// Round-robin across 4 categories × 4 components per category = 16 cvs total.
// Keeps the seed cheap while guaranteeing ≥ 1 row per category for S2.
const DEFAULT_COMPONENT_COUNT = 16;

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
    componentCount: DEFAULT_COMPONENT_COUNT,
    componentPrefix: "lic",
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.gotoLogin();
  await auth.login(seed.email, seed.password);
  return seed;
}

test.describe("@licenses project licenses tab", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("S1) Compliance grid renders license rows with valid categories", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectLicensesTab();

    // ≥ 1 license row arrived (server-reported total on the grid summary).
    const total = await portal.getLicenseRowCount();
    expect(total).toBeGreaterThanOrEqual(1);
    await expect(page.getByTestId("compliance-row").first()).toBeVisible();

    // Every rendered row carries one of the four license categories — the
    // grid replaces the old distribution chart, but the per-row
    // `data-category` is the locale-agnostic signal we assert on.
    const categories = await page
      .locator('[data-testid="compliance-row"]')
      .evaluateAll((rows) =>
        rows.map((r) => r.getAttribute("data-category")).filter(Boolean),
      );
    expect(categories.length).toBeGreaterThanOrEqual(1);
    for (const cat of categories) {
      expect(["forbidden", "conditional", "allowed", "unknown"]).toContain(cat);
    }
  });

  test("S2) category multi-filter narrows results and persists across reload", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectLicensesTab();

    const totalBefore = await portal.getLicenseRowCount();

    await portal.filterLicensesByCategory(["forbidden", "conditional"]);

    const totalAfter = await portal.getLicenseRowCount();
    // The seed mixes all four categories evenly → forbidden + conditional
    // is a strict subset.
    expect(totalAfter).toBeLessThanOrEqual(totalBefore);

    // Every visible row carries one of the two filtered categories.
    const visibleCategories = await page
      .locator('[data-testid="compliance-row"]')
      .evaluateAll((rows) =>
        rows.map((r) => r.getAttribute("data-category")).filter(Boolean),
      );
    for (const cat of visibleCategories) {
      expect(["forbidden", "conditional"]).toContain(cat);
    }

    // URL mirrors the filter as a CSV under `compliance_category`.
    const url = new URL(page.url());
    const cats = (url.searchParams.get("compliance_category") ?? "")
      .split(",")
      .sort();
    expect(cats).toEqual(["conditional", "forbidden"]);

    // Hard reload → filter survives.
    await page.reload();
    await portal.selectLicensesTab();
    const totalAfterReload = await portal.getLicenseRowCount();
    expect(totalAfterReload).toBe(totalAfter);
  });

  test("S3) clicking a row opens the drawer and renders meta + affected sections", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectLicensesTab();

    // Read the SPDX id of the first row (locale-agnostic) and open it via
    // the harness verb — this also asserts the URL flips to `?license=<id>`.
    // The grid only renders a `data-spdx-id` for licenses with a real SPDX id;
    // the seed uses `E2E-<CAT>-<suffix>` ids, so the first row always carries
    // one. We pick the first row that actually exposes a non-empty id to stay
    // robust against any LicenseRef-* row ordering ahead of it.
    const firstSpdxId = await page
      .locator('[data-testid="compliance-row"][data-spdx-id]:not([data-spdx-id=""])')
      .first()
      .getAttribute("data-spdx-id");
    expect(firstSpdxId).toBeTruthy();
    await portal.openLicenseDrawer(firstSpdxId as string);

    // Drawer contract: meta + affected sections both mount.
    await expect(page.getByTestId("license-drawer-meta")).toBeVisible();
    await expect(
      page.getByTestId("license-drawer-spdx-id"),
    ).toContainText(firstSpdxId as string);
    await expect(
      page.getByTestId("license-drawer-affected"),
    ).toBeVisible();
    // At least one affected component (the seed attaches every cv to a license).
    expect(
      await page.getByTestId("license-drawer-affected-row").count(),
    ).toBeGreaterThanOrEqual(1);

    // URL carries the selection.
    expect(new URL(page.url()).searchParams.get("license")).toBeTruthy();
  });

  test("S4) cross-link from drawer pivots to the Components tab drawer", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectLicensesTab();

    const firstSpdxId = await page
      .locator('[data-testid="compliance-row"][data-spdx-id]:not([data-spdx-id=""])')
      .first()
      .getAttribute("data-spdx-id");
    expect(firstSpdxId).toBeTruthy();
    await portal.openLicenseDrawer(firstSpdxId as string);

    // Capture the first affected row's component_version_id, then click it.
    const affectedRow = page
      .getByTestId("license-drawer-affected-row")
      .first();
    await expect(affectedRow).toBeVisible();
    const cvId = await affectedRow.getAttribute("data-component-version-id");
    expect(cvId).toBeTruthy();
    await affectedRow
      .getByTestId("license-drawer-affected-link")
      .click();

    // URL pivots: `?tab=components&drawer=<cv>` and `?license=<id>` is gone.
    await expect
      .poll(() => new URL(page.url()).searchParams.get("tab"), {
        timeout: 5_000,
      })
      .toBe("components");
    await expect
      .poll(() => new URL(page.url()).searchParams.get("drawer"), {
        timeout: 5_000,
      })
      .toBe(cvId);
    await expect
      .poll(() => new URL(page.url()).searchParams.get("license"), {
        timeout: 5_000,
      })
      .toBeNull();

    // ComponentDrawer is now visible (its testid is `component-drawer`).
    await expect(page.getByTestId("component-drawer")).toBeVisible();
  });
});
