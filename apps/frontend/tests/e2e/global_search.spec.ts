/**
 * Global search (⌘K) E2E — Phase H-2 (BomLens parity #10).
 *
 * The command palette gained cross-project Components + CVEs categories backed
 * by `GET /v1/search`. This drives the real palette against the dev stack:
 *
 *   S1 — typing a seeded component prefix surfaces the Components group and a
 *        hit deep-links to that project's Components tab (?tab=components&search=).
 *   S2 — typing a CVE substring surfaces the CVEs group.
 *
 * Team scoping is enforced server-side (`team_scope_filter`); each seed mints a
 * fresh team, so the actor only ever sees its own project's hits. Selectors are
 * `data-testid`; auth uses the refresh-cookie path (login-limiter friendly).
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 reachable from host PATH
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-search";

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
 * A component prefix unique to this test — a globally-unique purl plus a
 * search token that matches nothing else.
 *
 * This used to be `testInfo.testId` truncated to ten characters, which is why
 * every case after the first in this file silently skipped on CI from the day
 * it was written (the pre-existing S2 case had never once run — confirmed in
 * the 2026-07-30 nightly). Playwright's testId leads with a hash of the file,
 * so ten characters of it are IDENTICAL for every test in the same spec: the
 * second seed tried to insert component purls the first had already created,
 * tripped the unique constraint on `components.purl`, threw, and got swallowed
 * into a per-test skip that read as coverage.
 *
 * A per-run token plus a monotonic counter cannot collide that way. Each test
 * keeps its own seed — and therefore its own single-use refresh token, which a
 * shared fixture cannot provide (the token rotates on first use).
 */
const RUN_TOKEN = Date.now().toString(36);
let seedSequence = 0;

function uniquePrefix(testInfo: import("@playwright/test").TestInfo): string {
  seedSequence += 1;
  return `srch${RUN_TOKEN}s${seedSequence}r${testInfo.retry}`;
}

async function bootstrap(
  testInfo: import("@playwright/test").TestInfo,
  page: import("@playwright/test").Page,
): Promise<{ seed: SeedSummary; prefix: string } | null> {
  const prefix = uniquePrefix(testInfo);
  const seed = tryAcquireSeed(testInfo, {
    projectNames: [PROJECT_NAME],
    withScan: true,
    componentCount: 4, // → components + round-robin CVE findings to search
    componentPrefix: prefix,
    withRefreshToken: true,
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.loginViaRefreshCookie(seed.refresh_token!.token);
  return { seed, prefix };
}

async function openPalette(page: import("@playwright/test").Page): Promise<void> {
  await page.getByTestId("command-menu-trigger").click();
  await expect(page.getByTestId("command-menu-input")).toBeVisible();
}

test.describe("@global-search command palette", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("S1) a component search surfaces a hit that deep-links to the project", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page);
    if (boot === null) return;
    const { seed, prefix } = boot;
    const projectId = seed.project_ids[0];

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await openPalette(page);

    // Type the seeded component prefix; the Components group appears with a hit.
    await page.getByTestId("command-menu-input").fill(prefix);
    await expect(page.getByTestId("command-menu-group-components")).toBeVisible();
    const firstHit = page.getByTestId(`command-menu-component-${projectId}-0`);
    await expect(firstHit).toBeVisible();

    // Selecting it deep-links to the project's Components tab with the search
    // term applied.
    await firstHit.click();
    await expect(page).toHaveURL(new RegExp(`/projects/${projectId}\\b`));
    const url = new URL(page.url());
    expect(url.searchParams.get("tab")).toBe("components");
    // S1-5 — each tab owns a distinct search param. The palette must mint the
    // Components-specific key, not the shared `?search=` it used to write.
    expect(url.searchParams.get("components_search")).toBeTruthy();
    expect(url.searchParams.get("search")).toBeNull();
  });

  test("S3) a term typed on one tab does not follow the user to the next", async ({
    page,
  }, testInfo) => {
    // S1-5 regression. All four detail tabs used to write the same `?search=`
    // key, so a component name typed on Components silently filtered the CVE
    // list one click later — an empty table that looked like "no findings".
    const boot = await bootstrap(testInfo, page);
    if (boot === null) return;
    const { seed, prefix } = boot;
    const projectId = seed.project_ids[0];

    const portal = new PortalPage(page);
    await portal.gotoProjectDetail(projectId);

    // Type a component-only term on the Components tab and let it debounce
    // into the URL.
    await portal.openProjectTab("components");
    await page.getByTestId("components-search").fill(prefix);
    await expect(async () => {
      const url = new URL(page.url());
      expect(url.searchParams.get("components_search")).toBe(prefix);
    }).toPass();

    // Switch to Vulnerabilities: its own box must be empty, and the term must
    // still be parked under the Components key (so switching back restores it).
    await portal.openProjectTab("vulnerabilities");
    await expect(page.getByTestId("vulnerabilities-search")).toHaveValue("");
    const url = new URL(page.url());
    expect(url.searchParams.get("components_search")).toBe(prefix);
    expect(url.searchParams.get("vulnerabilities_search")).toBeNull();
  });

  test("S4) a legacy ?search= deep link still lands filtered", async ({
    page,
  }, testInfo) => {
    // Backward compatibility for links minted before S1-5: the active tab
    // reads the shared key as a fallback, then rewrites the URL to its own.
    const boot = await bootstrap(testInfo, page);
    if (boot === null) return;
    const { seed, prefix } = boot;
    const projectId = seed.project_ids[0];

    const portal = new PortalPage(page);
    await portal.gotoProjectDetail(projectId, {
      tab: "components",
      params: { search: prefix },
    });
    await expect(page.getByTestId("components-search")).toHaveValue(prefix);
    await expect(async () => {
      const url = new URL(page.url());
      expect(url.searchParams.get("components_search")).toBe(prefix);
      expect(url.searchParams.get("search")).toBeNull();
    }).toPass();
  });

  test("S2) a CVE search surfaces the CVEs group", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page);
    if (boot === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await openPalette(page);

    // The component-mode seed attaches CVE-2099-* findings; searching that
    // substring surfaces the CVEs group (team-scoped to this actor's project).
    await page.getByTestId("command-menu-input").fill("CVE-2099");
    await expect(page.getByTestId("command-menu-group-cves")).toBeVisible();
  });
});
