/**
 * Obligations E2E — Phase 3 PR #13 (re-targeted to the W9-#58 unified
 * Compliance grid + Reports tab).
 *
 * The standalone Obligations tab (with its own list, per-kind filter, and
 * obligation drawer) was absorbed into the read-only Compliance grid: each
 * `compliance-row` now carries the obligations attached to its license inline
 * as `compliance-obligation-chip` badges (`data-kind`), and the grid scopes
 * to obligations-bearing rows via the `Has obligations` toggle
 * (`?compliance_has_obligations=true`). The NOTICE download moved to the
 * Reports tab card. The harness verbs were updated accordingly, so the spec
 * keeps its original intent (rows + counts, obligations-only narrowing,
 * obligation kinds surface, NOTICE text + html download).
 *
 * Five `@obligations` scenarios:
 *
 *   S1 — Tab entry: obligations-bearing rows + inline obligation chips render
 *   S2 — `Has obligations` toggle narrows the grid and persists across reload
 *   S3 — Obligation chips carry a valid kind (`data-kind`)
 *   S4 — NOTICE download (text) from the Reports tab card
 *   S5 — NOTICE download (html) from the Reports tab card
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

test.describe("@obligations project obligations (Compliance grid)", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("S1) Compliance grid renders obligations-bearing rows with inline chips", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    // selectObligationsTab flips the `Has obligations` toggle on so the grid
    // scopes to rows that actually carry obligations.
    await portal.selectObligationsTab();

    const total = await portal.getObligationRowCount();
    expect(total).toBeGreaterThanOrEqual(1);

    // The first scoped row carries inline obligation chips.
    const firstRow = page
      .locator('[data-testid="compliance-row"][data-has-obligations="true"]')
      .first();
    await expect(firstRow).toBeVisible();
    expect(
      await firstRow.getByTestId("compliance-obligation-chip").count(),
    ).toBeGreaterThanOrEqual(1);
  });

  test("S2) the Has-obligations toggle narrows the grid and persists across reload", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);

    // Baseline: the full licenses view (toggle OFF) counts every license row.
    await portal.selectLicensesTab();
    const totalAll = await portal.getLicenseRowCount();

    // Scope to obligations-bearing rows (toggle ON).
    await portal.selectObligationsTab();
    const totalWithObligations = await portal.getObligationRowCount();

    // Not every license carries an obligation, so the scoped set is a subset.
    expect(totalWithObligations).toBeGreaterThanOrEqual(1);
    expect(totalWithObligations).toBeLessThanOrEqual(totalAll);

    // URL mirrors the toggle.
    expect(
      new URL(page.url()).searchParams.get("compliance_has_obligations"),
    ).toBe("true");

    // Hard reload → the scoped state survives.
    await page.reload();
    await portal.expectObligationsTabReady();
    const totalAfterReload = await portal.getObligationRowCount();
    expect(totalAfterReload).toBe(totalWithObligations);
  });

  test("S3) inline obligation chips carry a valid kind", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectObligationsTab();

    // The first obligations-bearing row exposes its obligation kinds via the
    // chips' `data-kind` (locale-agnostic — the chip label is translated, the
    // attribute is the SPDX/catalog kind verbatim).
    const kinds = await portal.firstRowObligationKinds();
    expect(kinds.length).toBeGreaterThanOrEqual(1);
    for (const kind of kinds) {
      expect(typeof kind).toBe("string");
      expect((kind ?? "").length).toBeGreaterThan(0);
    }
  });

  test("S4) NOTICE download (text) delivers a file with project name + at least one SPDX id", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);

    // The harness navigates to the Reports tab card (the NOTICE affordance's
    // new home) and downloads in text format.
    const { filename, body } = await portal.downloadNotice();
    expect(filename).toMatch(/^NOTICE-.+\.txt$/);
    // Header line carries the project name.
    expect(body).toContain(PROJECT_NAME);
    // The body lists the seed E2E SPDX prefix (`E2E-` from seed_e2e_user.py).
    expect(body).toMatch(/E2E-[A-Z]+-/);
  });

  test("S5) NOTICE download (html) delivers an .html file with markup", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);

    // Switch the Reports card's format select to "html" before downloading
    // (the harness drives `reports-card-notice-format`).
    const { filename, body } = await portal.downloadNotice("html");

    // Filename extension flips to .html (useNotice maps format → ext).
    expect(filename).toMatch(/^NOTICE-.+\.html$/);
    // The HTML rendering carries markup the text format does not — assert at
    // least one tag is present so we know the server honored ?format=html and
    // we didn't just relabel the text body. We stay lenient on which tag (the
    // NOTICE template is owned server-side) but require angle-bracket markup
    // plus the project name still being present.
    expect(body).toContain(PROJECT_NAME);
    expect(body).toMatch(/<[a-z!][^>]*>/i);
    // Sanity: the SPDX ids still surface in the HTML body.
    expect(body).toMatch(/E2E-[A-Z]+-/);
  });
});
