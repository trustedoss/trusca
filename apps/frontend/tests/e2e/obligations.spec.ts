/**
 * Obligations E2E — Phase 3 PR #13.
 *
 * Drives the project detail Obligations tab against the docker-compose dev
 * stack. Four `@obligations` scenarios:
 *
 *   S1 — Tab entry: list + per-kind distribution chips render
 *   S2 — Kind multi-filter sync (URL persists, narrows results)
 *   S3 — Drawer open: meta + obligation body + reference link
 *   S4 — NOTICE download (text): file is delivered with sane filename + body
 *   S5 — NOTICE download (html): the format select switches the response to
 *        an HTML body (`.html` filename + markup), proving the format toggle
 *        round-trips to the server
 *
 * Selectors live in `apps/frontend/tests/_harness/PortalPage.ts`. The
 * scenarios are EN-locale-agnostic — every assertion uses `data-testid`
 * or `data-*` attributes, never translated strings.
 *
 * Pre-requisites (auto-skip otherwise):
 *
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 reachable from host PATH (the seed.ts harness validates this)
 *
 * The seed `--component-count 8 --with-obligations` produces 4 e2e licenses
 * (one per category) × 7 obligations total (2/2/2/1 across forbidden /
 * conditional / allowed / unknown), guaranteeing both list rows and a
 * non-empty NOTICE body.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-obligations";
// 8 components → 2 per license category (round-robin), all four categories
// covered → all 7 seed obligations surfaced by the latest scan.
const DEFAULT_COMPONENT_COUNT = 8;

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
    componentPrefix: "obg",
    withObligations: true,
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.gotoLogin();
  await auth.login(seed.email, seed.password);
  return seed;
}

test.describe("@obligations project obligations tab", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("S1) Obligations tab renders the list and per-kind distribution chips", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectObligationsTab();

    const total = await portal.getObligationRowCount();
    expect(total).toBeGreaterThanOrEqual(1);
    await expect(page.getByTestId("obligation-row").first()).toBeVisible();

    // Distribution chips render (zero-count kinds are filtered out, so we
    // assert ≥ 1 chip rather than a fixed count).
    await expect(page.getByTestId("obligations-distribution")).toBeVisible();
    expect(
      await page.getByTestId("obligations-distribution-chip").count(),
    ).toBeGreaterThanOrEqual(1);
  });

  test("S2) kind multi-filter narrows results and persists across reload", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectObligationsTab();

    const totalBefore = await portal.getObligationRowCount();

    await portal.filterObligationsByKind(["attribution"]);

    const totalAfter = await portal.getObligationRowCount();
    expect(totalAfter).toBeLessThanOrEqual(totalBefore);

    const visibleKinds = await page
      .locator('[data-testid="obligation-row"]')
      .evaluateAll((rows) =>
        rows.map((r) => r.getAttribute("data-kind")).filter(Boolean),
      );
    for (const kind of visibleKinds) {
      expect(kind).toBe("attribution");
    }

    expect(new URL(page.url()).searchParams.get("kind")).toBe("attribution");

    await page.reload();
    await portal.selectObligationsTab();
    const totalAfterReload = await portal.getObligationRowCount();
    expect(totalAfterReload).toBe(totalAfter);
  });

  test("S3) clicking a row opens the drawer and renders meta + obligation body", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectObligationsTab();

    const firstRow = page.getByTestId("obligation-row").first();
    await expect(firstRow).toBeVisible();
    const obligationId = await firstRow.getAttribute("data-obligation-id");
    expect(obligationId).toBeTruthy();
    await portal.openObligationDrawer(obligationId as string);

    await expect(page.getByTestId("obligation-drawer-meta")).toBeVisible();
    await expect(page.getByTestId("obligation-drawer-text")).toBeVisible();
    expect(
      (await page.getByTestId("obligation-drawer-text").textContent()) ?? "",
    ).not.toBe("");
    expect(new URL(page.url()).searchParams.get("obligation")).toBeTruthy();
  });

  test("S4) NOTICE download delivers a file with project name + at least one SPDX id", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectObligationsTab();

    const { filename, body } = await portal.downloadNotice();
    expect(filename).toMatch(/^NOTICE-.+\.txt$/);
    // Header line carries the project name.
    expect(body).toContain(PROJECT_NAME);
    // The body lists the seed E2E SPDX prefix (`E2E-` from seed_e2e_user.py).
    expect(body).toMatch(/E2E-[A-Z]+-/);
  });

  test("S5) NOTICE download in HTML format delivers an .html file with markup", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectObligationsTab();

    // Switch the toolbar's format select to "html" before downloading. The
    // harness drives the `obligations-notice-format` select, so the spec stays
    // out of the selector business.
    const { filename, body } = await portal.downloadNotice("html");

    // Filename extension flips to .html (useNotice maps format → ext).
    expect(filename).toMatch(/^NOTICE-.+\.html$/);
    // The HTML rendering carries markup the text format does not — assert at
    // least one tag is present so we know the server honored ?format=html and
    // we didn't just relabel the text body. We stay lenient on which tag
    // (the NOTICE template is owned server-side) but require angle-bracket
    // markup plus the project name still being present.
    expect(body).toContain(PROJECT_NAME);
    expect(body).toMatch(/<[a-z!][^>]*>/i);
    // Sanity: the SPDX ids still surface in the HTML body.
    expect(body).toMatch(/E2E-[A-Z]+-/);
  });
});
