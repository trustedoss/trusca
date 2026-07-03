/**
 * Release compare (scan diff) E2E — Phase F (BomLens parity #6).
 *
 * The diff feature (`services/project_diff_service.py` +
 * `GET /projects/:id/diff` + `ComparePage.tsx`) landed with feature #28 but had
 * no end-to-end coverage. These `@compare` scenarios drive the real entry path
 * against the docker-compose dev stack:
 *
 *   S1 — single release: the Releases-tab Compare button is disabled (needs two).
 *   S2 — two releases: Compare navigates to the diff and renders the exact
 *        change sets the seed injected (1 added / 1 removed / 1 changed
 *        component; ≥1 introduced / ≥1 resolved finding).
 *   S3 — the swap control reverses base ↔ target.
 *
 * The seed extension `--scan-count 2` (this PR) seeds a SECOND succeeded scan on
 * the first project whose SCA posture differs from the first by that exact,
 * deterministic delta, so the diff change sets are non-empty and stable.
 *
 * All selectors are `data-testid` / `data-*` — EN/KO-locale-agnostic. Auth uses
 * the refresh-cookie path (never `POST /auth/login`) so a full suite run stays
 * under the 5/min login limiter.
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 reachable from host PATH (the seed.ts harness validates this)
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-compare";
// The seed requires >= 4 round-robin components for `--scan-count 2` (they are
// the "unchanged" set; the added/removed/changed/introduced/resolved deltas are
// seeded on dedicated, suffix-unique components on top). No `vulnerabilityCount`
// is needed — the introduced/resolved findings live on the delta components.
const COMPONENT_COUNT = 6;

/**
 * A component prefix unique to this test invocation. The round-robin seed
 * components use a globally-unique `pkg:npm/<prefix>-NNNNN` purl with NO
 * per-run suffix, so two seeds sharing a prefix against the same (persistent)
 * dev DB collide. Keying the prefix on the test id + retry keeps every
 * bootstrap in this file — across its three tests and any retries — collision-
 * free without depending on a between-test DB reset. (The delta components the
 * diff actually asserts on are already suffix-unique in the seed.)
 */
function uniquePrefix(testInfo: import("@playwright/test").TestInfo): string {
  const token = testInfo.testId.replace(/[^a-z0-9]/gi, "").slice(0, 12);
  return `cmp${token}r${testInfo.retry}`;
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

/** Seed (scanCount scans on the first project) + refresh-cookie login. */
async function bootstrap(
  testInfo: import("@playwright/test").TestInfo,
  page: import("@playwright/test").Page,
  scanCount: 1 | 2,
): Promise<SeedSummary | null> {
  const seed = tryAcquireSeed(testInfo, {
    projectNames: [PROJECT_NAME],
    withScan: true,
    scanCount,
    componentCount: COMPONENT_COUNT,
    componentPrefix: uniquePrefix(testInfo),
    withRefreshToken: true,
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.loginViaRefreshCookie(seed.refresh_token!.token);
  return seed;
}

async function openReleasesTab(
  page: import("@playwright/test").Page,
): Promise<PortalPage> {
  const portal = new PortalPage(page);
  await portal.gotoProjects();
  await portal.openProjectDetail(PROJECT_NAME);
  await portal.selectReleasesTab();
  return portal;
}

test.describe("@compare release diff", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("S1) a single release leaves the Compare button disabled", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, 1);
    if (seed === null) return;

    await openReleasesTab(page);

    // One succeeded scan → exactly one release row, Compare needs two.
    await expect(page.getByTestId("release-row")).toHaveCount(1);
    await expect(page.getByTestId("releases-compare-button")).toBeDisabled();
  });

  test("S2) two releases → Compare renders the injected change sets", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, 2);
    if (seed === null) return;
    // The seed must have surfaced the second (newer) scan id.
    expect(seed.second_scan_id).toBeTruthy();

    await openReleasesTab(page);

    // Two succeeded scans → two release rows, Compare enabled.
    await expect(page.getByTestId("release-row")).toHaveCount(2);
    const compareButton = page.getByTestId("releases-compare-button");
    await expect(compareButton).toBeEnabled();
    await compareButton.click();

    // Landed on the diff with both anchors pinned to the two seeded scans.
    const comparePage = page.getByTestId("compare-page");
    await expect(comparePage).toBeVisible();
    // ReleasesTab pins target = newest (second scan), base = previous (first).
    await expect(comparePage).toHaveAttribute(
      "data-base",
      seed.scan_ids![0],
    );
    await expect(comparePage).toHaveAttribute(
      "data-target",
      seed.second_scan_id as string,
    );

    // Component change sets — exactly the seed's 1 / 1 / 1 delta.
    await expect(page.getByTestId("compare-components-added")).toHaveAttribute(
      "data-count",
      "1",
    );
    await expect(page.getByTestId("compare-components-removed")).toHaveAttribute(
      "data-count",
      "1",
    );
    await expect(page.getByTestId("compare-components-changed")).toHaveAttribute(
      "data-count",
      "1",
    );

    // Vulnerability change sets — the seed introduces and resolves at least one.
    const introduced = await page
      .getByTestId("compare-vulns-introduced")
      .getAttribute("data-count");
    const resolved = await page
      .getByTestId("compare-vulns-resolved")
      .getAttribute("data-count");
    expect(Number(introduced)).toBeGreaterThanOrEqual(1);
    expect(Number(resolved)).toBeGreaterThanOrEqual(1);
  });

  test("S3) the swap control reverses base and target", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page, 2);
    if (seed === null) return;

    await openReleasesTab(page);
    await expect(page.getByTestId("release-row")).toHaveCount(2);
    await page.getByTestId("releases-compare-button").click();

    const comparePage = page.getByTestId("compare-page");
    await expect(comparePage).toBeVisible();
    const base = await comparePage.getAttribute("data-base");
    const target = await comparePage.getAttribute("data-target");
    expect(base).toBeTruthy();
    expect(target).toBeTruthy();
    expect(base).not.toBe(target);

    await page.getByTestId("compare-swap").click();

    // Anchors flip; the diff still renders (added/removed stay 1/1 by symmetry,
    // which is what a base↔target swap must produce for this seed).
    await expect(comparePage).toHaveAttribute("data-base", target as string);
    await expect(comparePage).toHaveAttribute("data-target", base as string);
    await expect(page.getByTestId("compare-components-added")).toHaveAttribute(
      "data-count",
      "1",
    );
    await expect(page.getByTestId("compare-components-removed")).toHaveAttribute(
      "data-count",
      "1",
    );
  });
});
