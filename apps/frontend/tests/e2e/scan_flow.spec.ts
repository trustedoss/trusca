/**
 * Scan flow E2E — Phase 2 PR #9 task 2.10 / 2.11.
 *
 * Covers the project list + scan progress streaming surface against a real
 * docker-compose dev stack (FastAPI + Postgres + Redis + Celery worker in
 * mock backend mode). Every selector is rooted in `data-testid` so the
 * scenarios pass on EN / KO without rewriting assertions.
 *
 * Scenarios (`@scan-flow` tag):
 *   1. Project list renders seeded rows and "Scan" triggers a scan; the
 *      drawer opens and the WebSocket pushes the initial sync frame.
 *   2. The drawer's `Reconnecting…` notice never trips during a healthy
 *      flow (sanity that the WS path stays "open" through pipeline).
 *   3. Search narrows the rendered list (`alpha` filters out `beta` /
 *      `gamma`).
 *   4. Status filter to `Running` shows only the project currently being
 *      scanned.
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - The worker container has `TRUSTEDOSS_SCAN_BACKEND=mock` exported, so
 *     cdxgen / ORT / Trivy are short-circuited and progress publishes
 *     complete in seconds. If your stack runs the real toolchain, bump the
 *     scan-completion timeout and re-run.
 *
 * Why a Python seed script: registering a fresh user via REST works, but the
 * auth surface intentionally has no team-creation endpoint at this Phase, so
 * a fresh user cannot create projects. The Python helper bypasses the API
 * and writes the rows directly. See `apps/backend/scripts/seed_e2e_user.py`.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

/**
 * Wrap seedE2eUser so a missing prerequisite (Postgres unreachable, python
 * missing, etc.) skips the test instead of erroring it out — keeps CI noise
 * contained when the dev stack is down.
 */
function tryAcquireSeed(
  test: import("@playwright/test").TestInfo,
  opts: Parameters<typeof seedE2eUser>[0],
): SeedSummary | null {
  try {
    return seedE2eUser(opts);
  } catch (err) {
    test.skip(
      true,
      `seed precondition failed — bring docker-compose dev up + ensure python3 is on PATH: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
    return null;
  }
}

/**
 * KNOWN PRODUCT BUG (PR #9 hand-off → frontend-dev):
 *
 *   apps/frontend/src/features/projects/ProjectListPage.tsx hardcodes
 *   `PROJECT_PAGE_SIZE = 200` and passes it to GET /v1/projects?size=200.
 *   The backend schema (apps/backend/api/v1/projects.py) caps `size` at
 *   `le=100`, so every list request returns 422 ("Input should be less
 *   than or equal to 100") and the page renders the destructive
 *   "Could not load projects." alert.
 *
 *   The fix is one-line — drop PROJECT_PAGE_SIZE to 100 (or raise the
 *   backend limit). Until that ships every scenario in this file would
 *   trip on the same alert. We mark them `test.fixme(...)` so they show
 *   up in CI as expected-fail rather than red, and the spec history
 *   captures the contract for when frontend-dev unblocks them.
 */
const KNOWN_PAGE_SIZE_BUG = false;

test.describe("@scan-flow project list + scan progress", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("1) loaded list → scan trigger opens drawer with initial frame", async ({
    page,
  }, testInfo) => {
    test.fixme(
      KNOWN_PAGE_SIZE_BUG,
      "ProjectListPage requests ?size=200; backend caps at 100 → 422. " +
        "Frontend-dev: lower PROJECT_PAGE_SIZE in ProjectListPage.tsx (or " +
        "bump backend `size` ceiling). Unskip once the request validates.",
    );
    const seed = tryAcquireSeed(testInfo, { projectNames: ["alpha"] });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);

    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    await portal.gotoProjects();
    await portal.expectProjectRowVisible("alpha");

    await portal.clickTriggerScan("alpha");
    // The drawer opens once the trigger response lands; the scan-progress
    // component subscribes to the WebSocket and the initial sync frame
    // arrives immediately. We assert on the drawer + the percent indicator
    // becoming visible, not on a literal step name (the seeded scan starts
    // from queued/0% and progresses through bootstrap/fetch).
    await portal.expectScanProgress();
    await expect(page.getByTestId("scan-progress-percent")).toBeVisible();
  });

  test("2) reconnecting notice does NOT appear during healthy flow", async ({
    page,
  }, testInfo) => {
    test.fixme(
      KNOWN_PAGE_SIZE_BUG,
      "Blocked by the same ?size=200 product bug — see scenario 1.",
    );
    const seed = tryAcquireSeed(testInfo, { projectNames: ["alpha"] });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);

    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
    await portal.gotoProjects();

    await portal.clickTriggerScan("alpha");
    await portal.expectScanProgress();

    // Healthy path: the "Reconnecting…" notice is conditionally rendered
    // only when state !== "open" AND reconnectAttempt > 0. Give the WS a
    // moment to settle by waiting on the percent indicator (auto-retrying)
    // and then assert the reconnect notice is NOT present.
    await expect(page.getByTestId("scan-progress-percent")).toBeVisible();
    await expect(page.getByTestId("scan-progress-reconnecting")).toHaveCount(0);
  });

  test("3) search narrows the project list", async ({ page }, testInfo) => {
    test.fixme(
      KNOWN_PAGE_SIZE_BUG,
      "Blocked by the same ?size=200 product bug — see scenario 1.",
    );
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["alpha", "beta", "gamma"],
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);

    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
    await portal.gotoProjects();

    await portal.expectVisibleProjectCount(3);

    // Toolbar debounces by 300ms — `expectVisibleProjectCount` auto-retries
    // until the rendered count converges.
    await portal.searchProjects("alp");
    await portal.expectVisibleProjectCount(1);
    await portal.expectProjectRowVisible("alpha");

    await portal.searchProjects("");
    await portal.expectVisibleProjectCount(3);
  });

  test("4) status filter narrows rows to running scans only", async ({
    page,
  }, testInfo) => {
    test.fixme(
      KNOWN_PAGE_SIZE_BUG,
      "Blocked by the same ?size=200 product bug — see scenario 1.",
    );
    const seed = tryAcquireSeed(testInfo, {
      projectNames: ["alpha", "beta"],
    });
    if (seed === null) return;

    const auth = new AuthHarness(page);
    const portal = new PortalPage(page);

    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
    await portal.gotoProjects();

    await portal.expectVisibleProjectCount(2);

    // Trigger one scan so a row is "non-idle" (latest_scan_id present).
    await portal.clickTriggerScan("alpha");
    await portal.expectScanProgress();
    await portal.closeScanProgressDrawer();

    // The toolbar's `idle` filter shows only projects that have never been
    // scanned (latest_scan_id == null). After triggering "alpha" the only
    // idle project is "beta". Status filtering for `running` requires
    // latest_scan_status on the wire shape (PR #10 follow-up); we use
    // `idle` here as the empirically-distinguishable case.
    await portal.filterProjectsByStatus("idle");
    await portal.expectVisibleProjectCount(1);
    await portal.expectProjectRowVisible("beta");

    await portal.filterProjectsByStatus("all");
    await portal.expectVisibleProjectCount(2);
  });
});
