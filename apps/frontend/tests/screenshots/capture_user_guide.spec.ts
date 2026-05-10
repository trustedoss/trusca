/**
 * Guide-screenshot capture — user-guide bulk.
 *
 * One `describe.serial(...)` per docs page. Auth is shared via
 * `global-setup.ts` + `use.storageState`, so each test starts already
 * logged in as the seeded super-admin. The seed creates two projects
 * (`SHARED_PROJECT_NAMES` from global-setup.ts) which the spec file
 * navigates between.
 *
 * Pages covered (matching `docs-site/docs/user-guide/*.md`):
 *   - auth-and-profile
 *   - projects
 *   - scans
 *   - components-and-licenses
 *   - vulnerabilities
 *   - approvals
 *   - sbom
 *   - obligations
 *   - notifications
 *   - integrations
 *
 * Captures whose pre-conditions cannot be staged from the shared seed
 * (e.g. a *failed* scan banner, a *suppressed*-state vulnerability, a
 * webhook delivery in progress) are intentionally omitted from this
 * PR and tracked under chore-backlog "Screenshots automation" → 부산물.
 */
import { test } from "@playwright/test";

import { ApprovalsHarness } from "../_harness/ApprovalsHarness";
import { AuthHarness } from "../_harness/auth";
import { IntegrationsHarness } from "../_harness/integrations";
import { NotificationsHarness } from "../_harness/NotificationsHarness";
import { PortalPage } from "../_harness/PortalPage";
import { ProfileHarness } from "../_harness/ProfileHarness";
import { ScansQueueHarness } from "../_harness/ScansQueueHarness";
import {
  applyAuthFromSeed,
  captureScreenshot,
  readSeedProjectNames,
} from "./_helpers";

/**
 * First project from globalSetup's persisted seed. Resolved lazily
 * (function, not module-level constant) because Playwright imports
 * spec files during test discovery — *before* `globalSetup` runs and
 * writes `.seed.json`. A constant evaluated at import time would
 * therefore throw "seed file missing".
 */
const primaryProject = (): string => readSeedProjectNames()[0];

// ════════════════════════════════════════════════════════════════════
// auth-and-profile (login / forgot are pre-auth → must clear state)
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/auth-and-profile (pre-auth)", () => {
  // These captures depict the LOGGED-OUT views, so we override the
  // shared storage state for this block by clearing cookies + localStorage
  // before navigation.
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("user-auth-login — login page", async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await captureScreenshot(page, "user-auth-login");
  });

  test("user-auth-forgot — forgot-password page", async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.gotoForgotPassword();
    await captureScreenshot(page, "user-auth-forgot");
  });
});

test.describe.serial("@screenshots user-guide/profile", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("user-profile-mounted — profile page header + identity card", async ({
    page,
  }) => {
    const profile = new ProfileHarness(page);
    await profile.gotoProfile();
    await captureScreenshot(page, "user-profile-mounted");
  });

  test("user-profile-connected-accounts — Connected Accounts panel", async ({
    page,
  }) => {
    const profile = new ProfileHarness(page);
    await profile.gotoProfile();
    await profile.expectConnectedAccounts(["github"]);
    await captureScreenshot(page, "user-profile-connected-accounts");
  });
});

// ════════════════════════════════════════════════════════════════════
// projects
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/projects", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("user-projects-list — project list with rows", async ({ page }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.expectProjectListVisible();
    await captureScreenshot(page, "user-projects-list");
  });

  test("user-projects-create-form — new project form mounted", async ({
    page,
  }) => {
    await page.goto("/projects/new");
    await page
      .getByTestId("project-create-form")
      .waitFor({ state: "visible", timeout: 10_000 });
    await captureScreenshot(page, "user-projects-create-form");
  });

  test("user-project-detail-overview — Overview tab on detail page", async ({
    page,
  }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(primaryProject());
    await portal.expectProjectDetailMounted();
    await captureScreenshot(page, "user-project-detail-overview");
  });
});

// ════════════════════════════════════════════════════════════════════
// scans (global queue)
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/scans", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  // FIXME(screenshots Session 2.5): `/scans` global queue page does not
  // settle in time during capture runs even with the shared seed pre-
  // loading scans. Either the page's initial fetch differs from the
  // seed shape or the harness mount predicate is too strict. Revisit
  // when ScansQueueHarness gains a richer mount predicate or when the
  // seed wires `latest_scan_id` more aggressively.
  test.fixme("user-scans-queue — global scan queue with seeded scan rows", async ({
    page,
  }) => {
    const scans = new ScansQueueHarness(page);
    await scans.gotoScans();
    await captureScreenshot(page, "user-scans-queue");
  });
});

// ════════════════════════════════════════════════════════════════════
// components-and-licenses
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/components-and-licenses", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("user-components-list — Components tab with virtualized rows", async ({
    page,
  }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(primaryProject());
    await portal.expectProjectDetailMounted();
    await portal.selectTab("components");
    await portal.expectComponentsTabReady();
    await captureScreenshot(page, "user-components-list");
  });

  test("user-licenses-donut — Licenses tab distribution", async ({ page }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(primaryProject());
    await portal.expectProjectDetailMounted();
    await portal.selectLicensesTab();
    await portal.expectLicensesTabReady();
    await captureScreenshot(page, "user-licenses-donut");
  });
});

// ════════════════════════════════════════════════════════════════════
// vulnerabilities
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/vulnerabilities", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("user-vulns-list — Vulnerabilities tab with seeded rows", async ({
    page,
  }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(primaryProject());
    await portal.expectProjectDetailMounted();
    await portal.selectVulnerabilitiesTab();
    await portal.expectVulnerabilitiesTabReady();
    await captureScreenshot(page, "user-vulns-list");
  });
});

// ════════════════════════════════════════════════════════════════════
// approvals (empty inbox — fresh super-admin without policy hits)
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/approvals", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  // FIXME(screenshots Session 2.5): `/approvals` page mount times out
  // under the bulk capture run. The harness expects either a tbody or
  // an empty card; neither resolves before the 10s timeout. Suspect the
  // approvals list endpoint requires a team scope the bulk seed does
  // not provision. Revisit alongside dedicated approvals e2e coverage.
  test.fixme("user-approvals-inbox — approvals page mounted (empty until policy hits)", async ({
    page,
  }) => {
    const approvals = new ApprovalsHarness(page);
    await approvals.gotoApprovals();
    await captureScreenshot(page, "user-approvals-inbox");
  });
});

// ════════════════════════════════════════════════════════════════════
// sbom
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/sbom", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("user-sbom-tab — SBOM tab on the project detail page", async ({
    page,
  }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(primaryProject());
    await portal.expectProjectDetailMounted();
    await portal.selectSbomTab();
    await captureScreenshot(page, "user-sbom-tab");
  });
});

// ════════════════════════════════════════════════════════════════════
// obligations
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/obligations", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("user-obligations-distribution — Obligations tab on the project detail page", async ({
    page,
  }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(primaryProject());
    await portal.expectProjectDetailMounted();
    await portal.selectObligationsTab();
    await portal.expectObligationsTabReady();
    await captureScreenshot(page, "user-obligations-distribution");
  });
});

// ════════════════════════════════════════════════════════════════════
// notifications
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/notifications", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("user-notifications-inbox — /notifications page mounted", async ({
    page,
  }) => {
    const notif = new NotificationsHarness(page);
    await notif.gotoNotifications();
    await captureScreenshot(page, "user-notifications-inbox");
  });

  // FIXME(screenshots Session 2.5): `notifications-prefs-section` does
  // not become visible during the capture run even though the e2e
  // notifications spec hits the same harness verb. Likely a viewport
  // / scroll-into-view race; capturing the prefs section will need a
  // dedicated harness verb that mounts the section without relying on
  // the inbox-then-prefs page composition.
  test.fixme("user-notifications-prefs — preferences screen", async ({ page }) => {
    const notif = new NotificationsHarness(page);
    await notif.gotoPreferences();
    await captureScreenshot(page, "user-notifications-prefs");
  });
});

// ════════════════════════════════════════════════════════════════════
// integrations
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/integrations", () => {
  test.beforeEach(async ({ page }) => {
    await applyAuthFromSeed(page);
  });

  test("user-integrations-keys — API keys section", async ({ page }) => {
    const integrations = new IntegrationsHarness(page);
    await integrations.goto();
    await integrations.expectMounted();
    await captureScreenshot(page, "user-integrations-keys");
  });

  test("user-integrations-key-create — Create API key dialog open", async ({
    page,
  }) => {
    const integrations = new IntegrationsHarness(page);
    await integrations.goto();
    await integrations.expectMounted();
    await integrations.clickCreate();
    await integrations.expectCreateDialogOpen();
    await captureScreenshot(page, "user-integrations-key-create");
  });
});
