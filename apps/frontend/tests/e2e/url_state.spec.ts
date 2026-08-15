/**
 * Filters survive a reload and a Back (B1).
 *
 * Five list screens kept their filters in component state. Reloading lost
 * them, the Back button left the page instead of undoing the last narrowing,
 * and a URL sent to a colleague showed them a different list. `/scans` was
 * the half-migrated case: it seeded its tab from the URL once and wrote back
 * on change, so the address bar and the screen agreed until Back, and then
 * quietly did not.
 *
 * This has to run in a real browser. The unit tests use MemoryRouter, which
 * keeps its own history stack and never reloads, so neither the reload nor
 * the browser's own Back button is reachable there.
 *
 * Pre-requisites (auto-skip otherwise), as the other authenticated specs:
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 *
 * Tagged `@url-state`.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { NotificationsHarness } from "../_harness/NotificationsHarness";
import { ScansQueueHarness } from "../_harness/ScansQueueHarness";
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

test.describe("@url-state list filters live in the address bar", () => {
  test.beforeEach(async ({ page }) => {
    await new AuthHarness(page).clearAuthState();
  });

  test("a chosen tab survives a reload and a Back undoes it", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["b1-url-state"],
      withScan: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const scans = new ScansQueueHarness(page);
    await scans.gotoScans();
    expect(await scans.activeTab()).toBe("all");

    await scans.selectTab("failed");
    expect(new URL(page.url()).searchParams.get("status")).toBe("failed");

    // The reload is the point: state held in the component would be gone.
    await page.reload();
    await scans.expectMounted();
    expect(await scans.activeTab()).toBe("failed");

    // And Back undoes the narrowing rather than leaving the page. This is
    // what the half-migrated version got wrong: the URL moved, the tab
    // stayed on failed.
    await page.goBack();
    await scans.expectMounted();
    expect(await scans.activeTab()).toBe("all");
    expect(new URL(page.url()).searchParams.get("status")).toBeNull();
  });

  test("the notification inbox filter survives a reload too", async ({
    page,
  }, testInfo) => {
    // A second screen, because the hook is shared but the wiring is not:
    // each screen decides which parameters it keeps and what it calls them.
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["b1-url-state-inbox"],
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const notifications = new NotificationsHarness(page);
    await notifications.gotoNotifications();

    const unreadOnly = page.getByTestId("notifications-unread-only");
    await expect(unreadOnly).not.toBeChecked();
    await unreadOnly.click();
    await expect(unreadOnly).toBeChecked();
    expect(new URL(page.url()).searchParams.get("unread")).toBe("1");

    await page.reload();
    await notifications.expectMounted();
    await expect(page.getByTestId("notifications-unread-only")).toBeChecked();

    await page.goBack();
    await notifications.expectMounted();
    await expect(
      page.getByTestId("notifications-unread-only"),
    ).not.toBeChecked();
  });

  test("a filter a link carries is applied, and one it invents is not", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["b1-url-state-deep-link"],
      withScan: true,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const scans = new ScansQueueHarness(page);

    await page.goto(`${scans.baseUrl}/scans?status=queued`);
    await scans.expectMounted();
    expect(await scans.activeTab()).toBe("queued");

    // A stale or hand-edited value falls back to the default instead of
    // reaching the backend, which would answer 422.
    await page.goto(`${scans.baseUrl}/scans?status=deleted&page=0`);
    await scans.expectMounted();
    expect(await scans.activeTab()).toBe("all");
  });
});
