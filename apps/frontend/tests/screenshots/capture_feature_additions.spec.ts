/**
 * Guide-screenshot capture — feature additions after the W9/0040 wave.
 *
 * Split from `capture_user_guide.spec.ts` so the two new cuts can be
 * (re)captured without re-rendering the whole bulk matrix (which would
 * churn the visual-regression baselines of untouched pages). Same
 * conventions: shared auth via global-setup storage state, seeded
 * projects resolved from `.seed.json`.
 *
 * Pages covered:
 *   - user-guide/vulnerabilities.md — "Group by upgrade" view (W9-#53)
 *   - user-guide/components-and-licenses.md — version currency (0040)
 */
import { test } from "@playwright/test";

import { PortalPage } from "../_harness/PortalPage";
import {
  applyAuthFromSeed,
  captureScreenshot,
  readSeedProjectNames,
} from "./_helpers";

const primaryProject = (): string => readSeedProjectNames()[0];

// ════════════════════════════════════════════════════════════════════
// vulnerabilities — Group by upgrade (W9-#53)
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/vulnerabilities (group by upgrade)", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  // Switch the toolbar's Group-by segmented control to "By upgrade" and
  // wait for at least one remediation cluster card. The seed's findings
  // carry fixed versions, so the cluster list always renders populated.
  test("user-vulns-group-by-upgrade — By upgrade view with remediation clusters", async ({
    page,
  }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(primaryProject());
    await portal.expectProjectDetailMounted();
    await portal.selectVulnerabilitiesTab();
    await portal.expectVulnerabilitiesTabReady();
    await page.getByTestId("vulnerabilities-group-by-upgrade").click();
    await page
      .getByTestId("vulnerabilities-upgrade-list")
      .waitFor({ state: "visible", timeout: 10_000 });
    await page
      .getByTestId("vulnerability-upgrade-cluster")
      .first()
      .waitFor({ state: "visible", timeout: 10_000 });
    await captureScreenshot(page, "user-vulns-group-by-upgrade");
  });
});

// ════════════════════════════════════════════════════════════════════
// components-and-licenses — version currency (0040)
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/components-and-licenses (version currency)", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  // Turn on the "Outdated only" toolbar toggle so the seeded
  // currency-fixture row (second component, `currency_state=outdated`)
  // is the visible content and the Currency column badge reads clearly.
  test("user-components-outdated — Outdated only filter with the Currency badge", async ({
    page,
  }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(primaryProject());
    await portal.expectProjectDetailMounted();
    await portal.selectTab("components");
    await portal.expectComponentsTabReady();
    await page.getByTestId("components-outdated-filter").click();
    await page
      .getByTestId("component-row-cell-currency")
      .first()
      .waitFor({ state: "visible", timeout: 10_000 });
    await captureScreenshot(page, "user-components-outdated");
  });
});
