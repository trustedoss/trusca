// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Full search page E2E (S3).
 *
 * What these are for beyond "the page renders":
 *
 *   F1 — the four tabs each return their own kind, and switching sheds the
 *        previous tab's facets rather than carrying a filter the new tab
 *        cannot display
 *   F2 — a facet chip narrows the result set and lands in the URL
 *   F3 — a saved search survives a round trip and reaches the dashboard
 *   F4 — the ⌘K palette leads here rather than dead-ending at its 20-hit cap
 *
 * Seeding: per test, with a run token plus a counter for the component prefix.
 * A truncated `testInfo.testId` is identical for every test in a spec (its
 * leading characters hash the file), which is how three other specs in this
 * suite silently skipped every case after their first.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { SearchPageHarness } from "../_harness/SearchPageHarness";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const RUN_TOKEN = Date.now().toString(36);
let seedSequence = 0;

function uniquePrefix(testInfo: import("@playwright/test").TestInfo): string {
  seedSequence += 1;
  return `srp${RUN_TOKEN}s${seedSequence}r${testInfo.retry}`;
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

async function bootstrap(
  testInfo: import("@playwright/test").TestInfo,
  page: import("@playwright/test").Page,
): Promise<{ seed: SeedSummary; prefix: string } | null> {
  const prefix = uniquePrefix(testInfo);
  const seed = tryAcquireSeed(testInfo, {
    projectNames: [`srch-${prefix}`],
    withScan: true,
    componentCount: 6,
    componentPrefix: prefix,
    vulnerabilityCount: 4,
    withRefreshToken: true,
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.loginViaRefreshCookie(seed.refresh_token!.token);
  return { seed, prefix };
}

test.describe("@search find-grade search page", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("F1) each tab returns its own kind and switching sheds facets", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page);
    if (boot === null) return;
    const { prefix } = boot;

    const search = new SearchPageHarness(page);
    await search.goto({ kind: "components", q: prefix });
    expect(await search.getKind()).toBe("components");
    expect(await search.getTotal()).toBeGreaterThan(0);

    // Narrow by a facet, then switch tabs: the facet must not follow, because
    // package_type means nothing to the vulnerabilities tab.
    await search.toggleFacet("package_type", "npm");
    expect(new URL(page.url()).searchParams.get("package_type")).toBe("npm");

    await search.selectKind("vulnerabilities");
    expect(await search.getKind()).toBe("vulnerabilities");
    expect(new URL(page.url()).searchParams.get("package_type")).toBeNull();

    await search.selectKind("projects");
    expect(await search.getKind()).toBe("projects");
  });

  test("F2) a facet chip narrows the results and lands in the URL", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page);
    if (boot === null) return;

    const search = new SearchPageHarness(page);
    await search.goto({ kind: "vulnerabilities", q: "CVE-2099" });
    const before = await search.getTotal();
    expect(before).toBeGreaterThan(1);

    // The chip's own count is what the click promises to leave behind.
    const promised = await search.getFacetCount("severity", "critical");
    await search.toggleFacet("severity", "critical");

    expect(new URL(page.url()).searchParams.get("severity")).toBe("critical");
    const after = await search.getTotal();
    expect(after).toBe(promised);
    expect(after).toBeLessThan(before);
  });

  test("F3) a saved search survives a reload and reaches the dashboard", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page);
    if (boot === null) return;
    const { prefix } = boot;

    const search = new SearchPageHarness(page);
    await search.goto({ kind: "components", q: prefix });
    const name = `saved-${prefix}`;
    await search.saveAs(name);

    // It shows up on the dashboard…
    await page.goto("http://localhost:5173/");
    const chip = page
      .getByTestId("saved-search-chip")
      .filter({ hasText: name });
    await expect(chip).toBeVisible();

    // …and opening it restores the search that was saved.
    await chip.getByRole("link", { name }).click();
    await search.expectMounted();
    expect(new URL(page.url()).searchParams.get("q")).toBe(prefix);
    expect(await search.getKind()).toBe("components");
  });

  test("F4) the palette leads to the full results rather than dead-ending", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page);
    if (boot === null) return;
    const { prefix } = boot;

    await page.goto("http://localhost:5173/projects");
    await page.getByTestId("command-menu-trigger").click();
    await expect(page.getByTestId("command-menu-input")).toBeVisible();
    await page.getByTestId("command-menu-input").fill(prefix);
    await expect(
      page.getByTestId("command-menu-group-components"),
    ).toBeVisible();

    await page.getByTestId("command-menu-see-all-components").click();

    const search = new SearchPageHarness(page);
    await search.expectMounted();
    expect(new URL(page.url()).searchParams.get("kind")).toBe("components");
    expect(new URL(page.url()).searchParams.get("q")).toBe(prefix);
    expect(await search.getTotal()).toBeGreaterThan(0);
  });

  test("F5) the palette opens the search page with nothing typed", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page);
    if (boot === null) return;

    // F4 covers the term-carrying path, which needs hits and therefore a term.
    // This is the other half: the page is absent from the sidebar, so a user
    // who has typed nothing must still have a way in or the surface is
    // undiscoverable.
    await page.goto("http://localhost:5173/projects");
    await page.getByTestId("command-menu-trigger").click();
    await expect(page.getByTestId("command-menu-input")).toBeVisible();

    await page.getByTestId("command-menu-open-search").click();

    const search = new SearchPageHarness(page);
    await search.expectMounted();
    expect(new URL(page.url()).searchParams.get("q")).toBeNull();
  });

  test("F6) each tab says which scans it draws from", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page);
    if (boot === null) return;
    const { prefix } = boot;

    // The contract this pins: components and projects reach through every scan
    // a project has run, vulnerabilities and licences resolve to the current
    // one. Users cannot infer that from the rows, and it is the reason this
    // tab's count differs from the /components inventory's.
    const search = new SearchPageHarness(page);
    await search.goto({ kind: "components", q: prefix });
    expect(await search.getScope()).toBe("all_scans");

    await search.selectKind("projects");
    expect(await search.getScope()).toBe("all_scans");

    await search.selectKind("vulnerabilities");
    expect(await search.getScope()).toBe("current_scan");

    await search.selectKind("licenses");
    expect(await search.getScope()).toBe("current_scan");
  });
});
