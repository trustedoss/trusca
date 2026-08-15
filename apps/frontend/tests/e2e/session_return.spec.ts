/**
 * Deep-link return through sign-in (A5).
 *
 * Following a link into the product while signed out sent the user to
 * /login, and signing in then put them on the dashboard. The address they
 * had followed was gone, and nothing said why they were asked to sign in.
 *
 * Two scenarios, both starting from a real deep link:
 *   1. Sign in, and land on the page that was asked for rather than the
 *      dashboard.
 *   2. The same, but the return target is a crafted off-site URL, which must
 *      be dropped rather than followed.
 *
 * The expiry banner is exercised by the unit tests: reproducing a real
 * refresh-token failure here would mean either waiting out a token or
 * reaching into the store, and the second is what `auth.spec.ts` already
 * does for the interceptor.
 *
 * Pre-requisites (auto-skip otherwise), as the other authenticated specs:
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 *
 * Tagged `@session-return`.
 */
import { expect, test } from "@playwright/test";

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

test.describe("@session-return signing in returns you to the deep link", () => {
  test.beforeEach(async ({ page }) => {
    await new AuthHarness(page).clearAuthState();
  });

  test("a deep link survives the sign-in it triggers", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["a5-deep-link"],
      withScan: true,
      componentCount: 3,
      componentPrefix: `a5dl${Date.now().toString(36)}`,
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);
    const target = `/projects/${seed.project_ids[0]}?tab=components`;

    // Signed out, follow the link. The guard sends us to sign in.
    await portal.goto(target);
    await expect(page).toHaveURL(/\/login$/);

    await auth.login(
      seed.email,
      seed.password,
      new RegExp(`/projects/${seed.project_ids[0]}`),
    );

    // Back where the link pointed, query string intact. Landing on the
    // dashboard here is the defect this covers.
    await expect(page).toHaveURL(new RegExp(`/projects/${seed.project_ids[0]}`));
    expect(new URL(page.url()).searchParams.get("tab")).toBe("components");
  });

  test("a return target pointing off-site is dropped", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["a5-open-redirect"],
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);

    // The shape an attacker sends: a path the router will route, carrying a
    // second origin in a place that looks internal. The browser reads a
    // leading double slash as an authority.
    await portal.goto("//evil.example/steal");
    await expect(page).toHaveURL(/\/login$/);

    await auth.login(seed.email, seed.password);

    // Anywhere on this origin is acceptable; leaving it is not.
    const landed = new URL(page.url());
    expect(landed.host).toBe(new URL(portal.baseUrl).host);
    expect(landed.pathname).not.toContain("evil.example");
  });
});
