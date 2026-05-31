/**
 * License waive E2E — c3 (per-component license waive on the Compliance tab).
 *
 * Drives the forbidden-license waive flow against the docker-compose dev
 * stack. The waive strip (`compliance-row-waive-strip`) renders only beneath
 * `forbidden`-category rows whose affected components carry a purl, so every
 * scenario seeds a project with forbidden components and enters the Compliance
 * tab via {@link PortalPage.openComplianceWaiveStrip}.
 *
 * New policy under test (c3): a waiver on a FORBIDDEN license relaxes the
 * build gate, so the backend (`LICENSE_WAIVE_MAX_DAYS`, default 90 days)
 * requires a capped expiry and rejects an open-ended one with a 422. The
 * dialog mirrors that — submit stays disabled until an expiry is set.
 *
 *   A — expiry-required guard : reason alone → submit disabled; +expiry → enabled
 *   B — waive succeeds        : reason + expiry → submit → "Waived" badge
 *   C — un-waive restores      : waived → un-waive → control back to data-waived="false"
 *   D — role gate             : a developer sees the trigger disabled + role-gated
 *
 * All selectors live in `apps/frontend/tests/_harness/PortalPage.ts`. The
 * scenarios are EN-locale-agnostic — every assertion uses `data-testid` /
 * `data-*` attributes, never translated strings.
 *
 * Auth / RBAC
 * -----------
 * The waive affordance is gated to `team_admin` / `super_admin`. The seed's
 * primary user is a plain `developer`, so scenarios A–C seed one extra
 * `team_admin` member (`extraMembers: 1, extraTeamAdmin: true`) and log in as
 * them; the project's `current_user_role` then resolves to `team_admin`.
 * Scenario D logs in as the primary developer to assert the disabled gate.
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 reachable from host PATH (the seed.ts harness validates this)
 *
 * The seed's `--component-count` round-robins components across the four
 * license categories (`_LICENSE_CATEGORY_CYCLE` in seed_e2e_user.py), so a
 * count of 16 yields 4 forbidden components — each rendered as a waivable
 * entry in the strip. Each scenario re-seeds with a per-test, per-retry
 * `componentPrefix` (`waive-<testId>-<retry>`) so the globally-unique
 * `uq_components_purl` space never collides across scenarios or retries.
 */
import { test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-license-waive";
// Round-robin across 4 categories × 4 = 16 components → 4 forbidden rows, each
// waivable. Cheap seed, guaranteed ≥ 1 forbidden waive strip.
const DEFAULT_COMPONENT_COUNT = 16;
// A bare yyyy-mm-dd the native <input type="date"> accepts. Well within the
// backend's LICENSE_WAIVE_MAX_DAYS (90d) cap and never in the past for the
// life of this fixture's CI run.
const EXPIRES_AT = "2099-01-01";

interface SeedOpts {
  /** Add one extra `team_admin` member so the waive gate is satisfied. */
  asTeamAdmin: boolean;
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

/**
 * Seed a project with forbidden components and authenticate. When
 * `asTeamAdmin` is set we log in as the seeded `team_admin` extra member (so
 * the waive gate opens); otherwise we log in as the primary `developer`.
 *
 * The extra member shares the primary user's password — see seed_e2e_user.py.
 */
async function bootstrap(
  testInfo: import("@playwright/test").TestInfo,
  page: import("@playwright/test").Page,
  { asTeamAdmin }: SeedOpts,
): Promise<SeedSummary | null> {
  // Each scenario re-seeds, and component purls are GLOBALLY unique
  // (`uq_components_purl`). A shared prefix would make every scenario after the
  // first collide on re-seed and auto-skip, so derive a per-test, per-retry
  // prefix from the stable test id (purl-safe: alnum only).
  const prefix = `waive-${testInfo.testId.replace(/[^a-z0-9]/gi, "")}-${testInfo.retry}`;
  const seed = tryAcquireSeed(testInfo, {
    projectNames: [PROJECT_NAME],
    withScan: true,
    componentCount: DEFAULT_COMPONENT_COUNT,
    componentPrefix: prefix,
    extraMembers: asTeamAdmin ? 1 : 0,
    extraTeamAdmin: asTeamAdmin,
  });
  if (seed === null) return null;

  let email = seed.email;
  if (asTeamAdmin) {
    const admin = seed.extra_members?.find((m) => m.role === "team_admin");
    if (admin == null) {
      testInfo.skip(true, "seed did not return a team_admin extra member");
      return null;
    }
    email = admin.email;
  }

  const auth = new AuthHarness(page);
  await auth.gotoLogin();
  await auth.login(email, seed.password);
  return seed;
}

test.describe("@critical license waive (Compliance tab)", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("A) forbidden waiver requires an expiry — submit gated until set", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, { asTeamAdmin: true });
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.openComplianceWaiveStrip();

    await portal.openLicenseWaive();

    // Reason only → the forbidden-license guard keeps submit disabled.
    await portal.fillWaive({ reason: "Legal reviewed — temporary exemption" });
    await portal.expectWaiveSubmitDisabled();

    // Supplying a capped expiry satisfies the guard → submit enables.
    await portal.fillWaive({
      reason: "Legal reviewed — temporary exemption",
      expires: EXPIRES_AT,
    });
    await portal.expectWaiveSubmitEnabled();
  });

  test("B) waiving a forbidden component shows the Waived badge", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, { asTeamAdmin: true });
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.openComplianceWaiveStrip();

    await portal.openLicenseWaive();
    await portal.fillWaive({
      reason: "Approved waiver pending upstream relicense",
      expires: EXPIRES_AT,
    });
    await portal.submitWaive();

    // Dialog commits + closes → the component flips to the waived badge.
    await portal.expectWaivedBadge();
  });

  test("C) un-waiving restores the waive control", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, { asTeamAdmin: true });
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.openComplianceWaiveStrip();

    // First waive so there is something to un-waive.
    await portal.openLicenseWaive();
    await portal.fillWaive({
      reason: "Temporary — will revert in this test",
      expires: EXPIRES_AT,
    });
    await portal.submitWaive();
    await portal.expectWaivedBadge();

    // Un-waive → the component returns to the not-waived state.
    await portal.unwaiveLicense();
    await portal.expectNotWaived();
  });

  test("D) a developer sees the waive trigger disabled + role-gated", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, { asTeamAdmin: false });
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.openComplianceWaiveStrip();

    // The primary user is a developer → the trigger renders disabled with the
    // role-gated marker rather than vanishing.
    await portal.expectWaiveRoleGated();
  });
});
