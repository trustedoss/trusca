/**
 * Organization-wide component inventory E2E (S2).
 *
 * Drives the real `/components` page against the dev stack. What these cases
 * are for, beyond "the page renders":
 *
 *   I1 — the page is reachable from the sidebar and reports its totals
 *   I2 — a package used by two projects is ONE row that counts both, which is
 *        the whole reason the surface exists
 *   I3 — the "used by" drawer names those projects
 *   I4 — search narrows the list and survives reload through the URL
 *
 * Seeding: one seed per test with a per-run unique component prefix. The
 * prefix must be unique per TEST, not per file — a truncated `testInfo.testId`
 * is identical for every test in a spec (its leading characters are a hash of
 * the file), which is how two other specs silently skipped every case after
 * their first. Counter + run token cannot collide.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { InventoryHarness } from "../_harness/InventoryHarness";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const RUN_TOKEN = Date.now().toString(36);
let seedSequence = 0;

function uniquePrefix(testInfo: import("@playwright/test").TestInfo): string {
  seedSequence += 1;
  return `inv${RUN_TOKEN}s${seedSequence}r${testInfo.retry}`;
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
  opts: { projectNames: string[]; shareComponents?: boolean },
): Promise<{ seed: SeedSummary; prefix: string } | null> {
  const prefix = uniquePrefix(testInfo);
  const seed = tryAcquireSeed(testInfo, {
    projectNames: opts.projectNames,
    withScan: true,
    componentCount: 3,
    componentPrefix: prefix,
    // The default fixture anchors every component on the FIRST project, so
    // "one package, two projects" — the shape this page exists to report —
    // has to be asked for explicitly. CI caught this: the first draft of I2
    // and I3 assumed a fixture behaviour that does not exist.
    shareComponents: opts.shareComponents ?? false,
    withRefreshToken: true,
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.loginViaRefreshCookie(seed.refresh_token!.token);
  return { seed, prefix };
}

test.describe("@inventory organization-wide components", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("I1) the sidebar reaches the inventory and it reports a total", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page, { projectNames: ["inv-solo"] });
    if (boot === null) return;

    const inventory = new InventoryHarness(page);
    await page.goto("http://localhost:5173/projects");
    await inventory.gotoViaSidebar();

    await expect(page).toHaveURL(/\/components$/);
    expect(await inventory.getTotal()).toBeGreaterThan(0);
  });

  test("I2) a package used by two projects is one row counting both", async ({
    page,
  }, testInfo) => {
    // The seed attaches the SAME component prefix to both projects, so the
    // inventory must collapse them rather than list the package twice.
    const boot = await bootstrap(testInfo, page, {
      projectNames: ["inv-one", "inv-two"],
      shareComponents: true,
    });
    if (boot === null) return;
    const { prefix } = boot;

    const inventory = new InventoryHarness(page);
    await inventory.goto();
    await inventory.search(prefix);

    const firstPackage = `${prefix}-00000`;
    await inventory.expectRowVisible(firstPackage);
    // Exactly one row for that package — not one per project.
    await expect(inventory.row(firstPackage)).toHaveCount(1);
    expect(await inventory.getProjectCount(firstPackage)).toBe(2);
  });

  test("I3) the drawer names the projects that use a package", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page, {
      projectNames: ["inv-drawer-a", "inv-drawer-b"],
      shareComponents: true,
    });
    if (boot === null) return;
    const { prefix } = boot;

    const inventory = new InventoryHarness(page);
    await inventory.goto();
    await inventory.search(prefix);
    await inventory.openUsage(`${prefix}-00000`);

    await expect(inventory.drawerRows()).toHaveCount(2);
    const names = (await inventory.getDrawerProjectNames()).join(" ");
    expect(names).toContain("inv-drawer-a");
    expect(names).toContain("inv-drawer-b");
  });

  test("I4) search narrows the list and survives a reload", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page, {
      projectNames: ["inv-search"],
    });
    if (boot === null) return;
    const { prefix } = boot;

    const inventory = new InventoryHarness(page);
    await inventory.goto();
    const unfiltered = await inventory.getTotal();

    await inventory.search(`${prefix}-00001`);
    const filtered = await inventory.getTotal();
    expect(filtered).toBeLessThan(unfiltered);
    await inventory.expectRowVisible(`${prefix}-00001`);
    await inventory.expectRowAbsent(`${prefix}-00002`);

    // The term lives in the URL, so a reload restores the same view.
    await page.reload();
    await inventory.expectMounted();
    expect(await inventory.getTotal()).toBe(filtered);
    await expect(page.getByTestId("inventory-search")).toHaveValue(
      `${prefix}-00001`,
    );
  });

  test("I5) a fruitless search offers the wider scan history", async ({
    page,
  }, testInfo) => {
    const boot = await bootstrap(testInfo, page, {
      projectNames: ["inv-nomatch"],
    });
    if (boot === null) return;

    const inventory = new InventoryHarness(page);
    await inventory.goto();

    // With no term the empty state stays generic — there is nothing to carry
    // to the other surface, so offering the trip would be noise.
    await inventory.search("zzz-no-such-package-anywhere");
    await expect(page.getByTestId("inventory-empty")).toBeVisible();
    await expect(inventory.searchHistoryLink()).toBeVisible();

    // This page shows each project's latest scan; the search page reaches back
    // through the history. Following the link keeps the term and lands on the
    // components tab, which is the one that answers the same question.
    await inventory.followSearchHistory();
    const url = new URL(page.url());
    expect(url.searchParams.get("q")).toBe("zzz-no-such-package-anywhere");
    expect(url.searchParams.get("kind")).toBe("components");
  });
});
