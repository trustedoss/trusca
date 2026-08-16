/**
 * The three tables export what is on the screen (B5).
 *
 * Everything below the browser is covered elsewhere: the backend tests pin
 * the access check and the column contract, the unit tests pin the filters
 * the client sends and the message a refusal produces. What only a real
 * browser can answer is whether a file actually arrives, because that path
 * runs through a bearer-token fetch, a blob, and a synthetic anchor click,
 * and none of those exist under jsdom.
 *
 * Pre-requisites (auto-skip otherwise), as the other authenticated specs:
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 *
 * Tagged `@csv-export`.
 */
import { expect, test, type Download } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

function tryAcquireSeed(
  testInfo: import("@playwright/test").TestInfo,
  opts: Parameters<typeof seedE2eUser>[0],
): SeedSummary | null {
  try {
    return seedE2eUser(opts);
  } catch (err) {
    testInfo.skip(
      true,
      `seed precondition failed: bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

/** Click an export button and wait for the browser to take the file. */
async function exportFrom(
  page: import("@playwright/test").Page,
  testId: string,
): Promise<Download> {
  const downloadPromise = page.waitForEvent("download", { timeout: 15_000 });
  await page.getByTestId(testId).click();
  return downloadPromise;
}

test.describe("@csv-export the filtered table leaves as a file", () => {
  test.beforeEach(async ({ page }) => {
    await new AuthHarness(page).clearAuthState();
  });

  test("the vulnerabilities tab exports, and the file carries the header", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["b5-vuln-export"],
      withScan: true,
      componentCount: 3,
      componentPrefix: `b5v${Date.now().toString(36)}`,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    await portal.goto(`/projects/${seed.project_ids[0]}?tab=vulnerabilities`);
    await expect(page.getByTestId("vulnerabilities-toolbar")).toBeVisible();

    const download = await exportFrom(page, "vulnerabilities-export-csv");
    expect(download.suggestedFilename()).toMatch(/\.csv$/);

    // Read it back: a download event proves a blob arrived, not that the
    // blob is a CSV. The header row is the column contract the backend
    // tests pin, seen from the other end.
    const stream = await download.createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(Buffer.from(chunk));
    const body = Buffer.concat(chunks).toString("utf8");
    // The BOM is what makes Excel on a Korean locale read this as UTF-8.
    expect(body.charCodeAt(0)).toBe(0xfeff);
    expect(body).toContain("cve_id,severity,cvss_score");

    await expect(
      page.locator('[data-toast-key="csv_started"][data-tone="success"]'),
    ).toBeVisible();
  });

  test("the components tab exports", async ({ page }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["b5-components-export"],
      withScan: true,
      componentCount: 3,
      componentPrefix: `b5c${Date.now().toString(36)}`,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    await portal.goto(`/projects/${seed.project_ids[0]}?tab=components`);
    await expect(page.getByTestId("components-toolbar")).toBeVisible();

    const download = await exportFrom(page, "components-export-csv");
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
  });

  test("the inventory page exports what its filter narrowed to", async ({
    page,
  }, testInfo) => {
    const prefix = `b5i${Date.now().toString(36)}`;
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["b5-inventory-export"],
      withScan: true,
      componentCount: 3,
      componentPrefix: prefix,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(page);
    // `/components` is the route; "inventory" is what the feature directory
    // and the API call it, and the sidebar item says Components.
    await portal.goto("/components");
    await expect(page.getByTestId("inventory-toolbar")).toBeVisible();

    // Narrow to this run's own components, then check the file honoured it.
    // This is the whole promise of the feature: the file is the screen.
    await page.getByTestId("inventory-search").fill(prefix);
    await expect
      .poll(async () => page.getByTestId("inventory-row").count(), {
        timeout: 10_000,
      })
      .toBeGreaterThan(0);

    const download = await exportFrom(page, "inventory-export-csv");
    const stream = await download.createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(Buffer.from(chunk));
    const body = Buffer.concat(chunks).toString("utf8");

    expect(body).toContain("name,package_type,purl");
    expect(body).toContain(prefix);
  });
});
