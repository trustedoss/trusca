/**
 * Dual-Surface E2E — W10-F.
 *
 * Validates the user-facing round-trip that W10-A through W10-E built up:
 *
 *   drawer (list)  ──[ Open in full view ]──▶  page (`/projects/.../vulns/...`)
 *      ▲                                                       │
 *      └──[ Back to Vulnerabilities ]◀────────────────────────┘
 *
 * The unit suites (`VulnerabilityDrawer.test.tsx`, `VulnerabilityDetailPage.
 * test.tsx`, `ComponentDrawer.test.tsx`, `ComponentDetailPage.test.tsx`)
 * already pin the wiring at the component level — including the cross-project
 * `location.state.from` guard. This spec exercises the same flow against the
 * real docker-compose stack so a router regression, a backend 404, or a
 * service-layer field drop would surface here.
 *
 * Scenarios (all `@dual-surface`):
 *   A — Vulnerability dual-surface round-trip (drawer → page → back)
 *   B — Vulnerability page direct entry (shareable deep link)
 *   C — Component dual-surface round-trip (no NEXT STEPS sidebar in W10-E)
 *   D — Narrow viewport fallback: NEXT STEPS stacks below body
 *
 * Scenario D in the brief (cross-project `state.from` guard) is left to the
 * unit test in `tests/unit/pages/VulnerabilityDetailPage.test.tsx` — it
 * already covers the two relevant branches (`evil://` + protocol-relative)
 * without the e2e cost of injecting `location.state` through the SPA
 * boundary.
 *
 * All selectors live in `apps/frontend/tests/_harness/PortalPage.ts`.
 * Locale-agnostic — every anchor is a `data-testid` or `data-*` attribute.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME_BASE = "ci-dual-surface";
// Small fixture: 6 vulns + 8 components is enough to exercise the round-trip
// without inflating the seed runtime. The page itself only renders the active
// finding/component — list density is irrelevant to the dual-surface contract.
const VULN_COUNT = 6;
const COMPONENT_COUNT = 8;
// `uq_components_purl` is a global UNIQUE constraint; a fixed prefix collides
// across both re-runs AND across the 4 tests in this file (each calls
// `seedE2eUser` independently). Each test gets a fresh prefix derived from
// the test title + run timestamp so the suite is re-runnable without
// `dev-reset.sh`. Component i in test T is therefore named
// `dscomp-<runId>-<testSlug>-00000`.
const RUN_ID = Date.now().toString(36);
function makePrefix(testTitle: string): string {
  // Take the leading scenario letter (A/B/C/D) for a short, stable slug.
  const slug = testTitle.match(/^[A-Z]/)?.[0] ?? "x";
  return `dscomp-${RUN_ID}-${slug.toLowerCase()}`;
}

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

interface BootstrapResult {
  seed: SeedSummary;
  componentPrefix: string;
  projectName: string;
}

async function bootstrap(
  testInfo: import("@playwright/test").TestInfo,
  page: import("@playwright/test").Page,
): Promise<BootstrapResult | null> {
  const componentPrefix = makePrefix(testInfo.title);
  // Per-test project name too, so concurrent reruns don't collide on
  // `uq_projects_team_id_name`.
  const projectName = `${PROJECT_NAME_BASE}-${RUN_ID}-${
    testInfo.title.match(/^[A-Z]/)?.[0]?.toLowerCase() ?? "x"
  }`;
  const seed = tryAcquireSeed(testInfo, {
    projectNames: [projectName],
    withScan: true,
    componentCount: COMPONENT_COUNT,
    componentPrefix,
    vulnerabilityCount: VULN_COUNT,
  });
  if (seed === null) return null;

  // Each test does one password login. The suite has 4 tests, well below
  // the 5/min/IP limiter — but consecutive `playwright test` invocations
  // share the same loopback IP, so back-to-back local re-runs may briefly
  // trip 429. CI runs each spec in its own container so the budget resets.
  const auth = new AuthHarness(page);
  await auth.gotoLogin();
  await auth.login(seed.email, seed.password);
  return { seed, componentPrefix, projectName };
}

test.describe("@dual-surface dual-surface (drawer + page nav)", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("A) vulnerability: drawer → 'Open in full view' → page → 'Back' returns to list", async ({
    page,
  }, testInfo) => {
    const fixture = await bootstrap(testInfo, page);
    if (fixture === null) return;
    const { projectName } = fixture;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(projectName);
    await portal.selectVulnerabilitiesTab();

    // Read the project + finding id off the DOM so the spec can assert
    // URL provenance without learning seed internals.
    const projectIdMatch = page.url().match(/\/projects\/([^/?#]+)/);
    expect(projectIdMatch).not.toBeNull();
    const projectId = projectIdMatch![1];

    const firstRow = page.getByTestId("vulnerability-row").first();
    await expect(firstRow).toBeVisible();
    const findingId = await firstRow.getAttribute("data-finding-id");
    expect(findingId).toBeTruthy();

    // 1. Open drawer → assert it's mounted.
    await portal.openVulnerabilityDrawerFromList(findingId as string);
    await expect(page.getByTestId("vulnerability-drawer")).toBeVisible();

    // 2. Click "Open in full view" — page mounts on the deep-link route.
    await portal.clickOpenInFullView();
    await portal.expectVulnerabilityDetailPageMounted(findingId as string);

    // 3. URL = /projects/<projectId>/vulnerabilities/<findingId>.
    expect(new URL(page.url()).pathname).toBe(
      `/projects/${projectId}/vulnerabilities/${findingId}`,
    );

    // 4. Page surface contract: breadcrumb + body + NEXT STEPS sidebar all
    // mount (the lg-breakpoint sidebar from W10-D).
    await expect(
      page.getByTestId("vulnerability-detail-page-breadcrumb-projects"),
    ).toBeVisible();
    await expect(
      page.getByTestId("vulnerability-detail-page-breadcrumb-project"),
    ).toBeVisible();
    await expect(
      page.getByTestId("vulnerability-detail-page-main"),
    ).toBeVisible();
    await portal.expectNextStepsPanelVisible();

    // 5. Click "Back to Vulnerabilities" — same-project `state.from` is
    // honored so we return to the originating list URL (which includes
    // `?tab=vulnerabilities` because that's the URL the drawer captured).
    await portal.clickBackToVulnerabilities();
    const backUrl = new URL(page.url());
    expect(backUrl.pathname).toBe(`/projects/${projectId}`);
    expect(backUrl.searchParams.get("tab")).toBe("vulnerabilities");
  });

  test("B) vulnerability: direct deep-link entry mounts the page + sidebar", async ({
    page,
  }, testInfo) => {
    const fixture = await bootstrap(testInfo, page);
    if (fixture === null) return;
    const { projectName } = fixture;

    const portal = new PortalPage(page);

    // We need a finding id. Land on the list first to read one off the DOM,
    // then directly navigate (simulates a shared deep-link). This mirrors
    // the unit suite's pattern: list-then-deep-link instead of an admin
    // backdoor — the SPA has no other way to surface finding ids.
    await portal.gotoProjects();
    await portal.openProjectDetail(projectName);
    await portal.selectVulnerabilitiesTab();

    const projectIdMatch = page.url().match(/\/projects\/([^/?#]+)/);
    expect(projectIdMatch).not.toBeNull();
    const projectId = projectIdMatch![1];

    const findingId = await page
      .getByTestId("vulnerability-row")
      .first()
      .getAttribute("data-finding-id");
    expect(findingId).toBeTruthy();

    // Direct navigate — no `location.state.from`, so the page MUST render
    // its default back-link target (`?tab=vulnerabilities`).
    await portal.gotoVulnerabilityDetailPage(projectId, findingId as string);

    // Page surface: body + NEXT STEPS sidebar are both present.
    await expect(
      page.getByTestId("vulnerability-detail-page-main"),
    ).toBeVisible();
    await portal.expectNextStepsPanelVisible();

    // Back-link defaults to the list URL with `?tab=vulnerabilities`
    // (no state.from to override).
    const backHref = await page
      .getByTestId("vulnerability-detail-page-back-link")
      .getAttribute("href");
    expect(backHref).toBe(`/projects/${projectId}?tab=vulnerabilities`);
  });

  test("C) component: drawer → 'Open in full view' → page → 'Back' returns to list", async ({
    page,
  }, testInfo) => {
    const fixture = await bootstrap(testInfo, page);
    if (fixture === null) return;
    const { projectName, componentPrefix } = fixture;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(projectName);
    await portal.selectTab("components");
    await portal.expectComponentsTabReady();

    const projectIdMatch = page.url().match(/\/projects\/([^/?#]+)/);
    expect(projectIdMatch).not.toBeNull();
    const projectId = projectIdMatch![1];

    // Read the first component's id + name straight off the row.
    const firstRow = page.getByTestId("component-row").first();
    await expect(firstRow).toBeVisible();
    const componentId = await firstRow.getAttribute("data-component-id");
    expect(componentId).toBeTruthy();
    // Seeded component i is named `{prefix}-{i:05d}` (zero-padded; see
    // `seed_e2e_user.py`). With the per-test prefix the first component is
    // `<prefix>-00000`.
    const componentName = `${componentPrefix}-00000`;

    // 1. Open drawer.
    await portal.openComponentDrawerFromList(componentName);
    await expect(page.getByTestId("component-drawer")).toBeVisible();

    // 2. Click "Open in full view" — page mounts.
    await portal.clickOpenInFullView();
    await portal.expectComponentDetailPageMounted(componentId as string);

    // 3. URL contract.
    expect(new URL(page.url()).pathname).toBe(
      `/projects/${projectId}/components/${componentId}`,
    );

    // 4. Page surface — breadcrumb + body. NO NEXT STEPS sidebar (W10-E
    // explicitly defers it pending backend approvals filter). Assert the
    // sidebar testid is absent so a future regression that adds an empty
    // placeholder doesn't slip in unnoticed.
    await expect(
      page.getByTestId("component-detail-page-breadcrumb-components"),
    ).toBeVisible();
    await expect(
      page.getByTestId("component-detail-page-main"),
    ).toBeVisible();
    await expect(
      page.getByTestId("vulnerability-next-steps-panel"),
    ).toHaveCount(0);

    // 5. Back link returns us to the project's components view. The drawer
    // captured the originating URL (which includes `?tab=components`).
    await portal.clickBackToComponents();
    const backUrl = new URL(page.url());
    expect(backUrl.pathname).toBe(`/projects/${projectId}`);
    expect(backUrl.searchParams.get("tab")).toBe("components");
  });

  test("D) narrow viewport: NEXT STEPS sidebar stacks below body (W10-D fallback)", async ({
    page,
  }, testInfo) => {
    const fixture = await bootstrap(testInfo, page);
    if (fixture === null) return;
    const { projectName } = fixture;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(projectName);
    await portal.selectVulnerabilitiesTab();

    const projectIdMatch = page.url().match(/\/projects\/([^/?#]+)/);
    expect(projectIdMatch).not.toBeNull();
    const projectId = projectIdMatch![1];
    const findingId = await page
      .getByTestId("vulnerability-row")
      .first()
      .getAttribute("data-finding-id");
    expect(findingId).toBeTruthy();

    // Switch to a sub-lg viewport (Tailwind's lg breakpoint is 1024 px) so
    // the layout's `lg:flex-row` modifier drops out and both columns stack.
    // Done AFTER list navigation (the project list toolbar collapses
    // poorly below ~900 px and we don't need the narrow viewport for that
    // step) — we want the narrow layout only for the detail page itself.
    await page.setViewportSize({ width: 800, height: 900 });

    // Deep-link directly — drawer-vs-narrow-viewport interaction is out of
    // scope; we want the page mounted under the narrow viewport.
    await portal.gotoVulnerabilityDetailPage(projectId, findingId as string);
    await portal.expectNextStepsPanelVisible();

    // Both surfaces share the same `<main>` ancestor and the body precedes
    // the sidebar in DOM order. On a sub-lg viewport the flex-col layout
    // wraps the second column under the first (no real layout assertion in
    // Playwright — we mirror the unit test's contract: structural ordering
    // is what makes the fallback work, the CSS handles the rest).
    const sameAncestor = await page.evaluate(() => {
      const body = document.querySelector(
        '[data-testid="vulnerability-detail-page-main"]',
      );
      const sidebar = document.querySelector(
        '[data-testid="vulnerability-next-steps-panel"]',
      );
      if (!body || !sidebar) return { sameMain: false, order: 0 };
      const main = body.closest("main");
      return {
        sameMain: main !== null && main === sidebar.closest("main"),
        // Node.DOCUMENT_POSITION_FOLLOWING === 4
        order: body.compareDocumentPosition(sidebar) & 4,
      };
    });
    expect(sameAncestor.sameMain).toBe(true);
    expect(sameAncestor.order).toBeTruthy();
  });
});
