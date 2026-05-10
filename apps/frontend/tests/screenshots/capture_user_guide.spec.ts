/**
 * Guide-screenshot capture — user-guide bulk.
 *
 * One `describe.serial(...)` per docs page. Each block seeds an isolated
 * super-admin (per-page `componentPrefix`), logs in once, and emits the
 * captures the corresponding Markdown page references.
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
 * Captures whose pre-conditions cannot be staged from the standard seed
 * (e.g. a *failed* scan banner, a *suppressed*-state vulnerability, a
 * webhook delivery in progress) are intentionally omitted from this PR
 * and tracked under chore-backlog "Screenshots automation" → backlog.
 */
import { expect, test } from "@playwright/test";

import { ApprovalsHarness } from "../_harness/ApprovalsHarness";
import { AuthHarness } from "../_harness/auth";
import { IntegrationsHarness } from "../_harness/integrations";
import { NotificationsHarness } from "../_harness/NotificationsHarness";
import { PortalPage } from "../_harness/PortalPage";
import { ProfileHarness } from "../_harness/ProfileHarness";
import { ScansQueueHarness } from "../_harness/ScansQueueHarness";
import { type SeedSummary } from "../_harness/seed";
import {
  captureScreenshot,
  withSeedBeforeAll,
} from "./_helpers";

// ════════════════════════════════════════════════════════════════════
// auth-and-profile
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/auth-and-profile", () => {
  let seed: SeedSummary | null = null;
  withSeedBeforeAll(
    "user-auth-and-profile",
    ["screenshots-auth"],
    (s) => { seed = s; },
  );

  test.beforeEach(async ({ page }) => {
    if (seed === null) test.skip(true, "seed not available");
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

  test("user-profile-mounted — profile page header + identity card", async ({
    page,
  }) => {
    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed!.email, seed!.password);
    const profile = new ProfileHarness(page);
    await profile.gotoProfile();
    await captureScreenshot(page, "user-profile-mounted");
  });

  test("user-profile-connected-accounts — Connected Accounts panel", async ({
    page,
  }) => {
    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed!.email, seed!.password);
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
  let seed: SeedSummary | null = null;
  withSeedBeforeAll(
    "user-projects",
    ["screenshots-projects-alpha", "screenshots-projects-beta"],
    (s) => { seed = s; },
  );

  test.beforeEach(async ({ page }) => {
    if (seed === null) test.skip(true, "seed not available");
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed!.email, seed!.password);
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
    await page.goto("http://localhost:5173/projects/new");
    await expect(page.getByTestId("project-create-form")).toBeVisible({
      timeout: 10_000,
    });
    await captureScreenshot(page, "user-projects-create-form");
  });

  test("user-project-detail-overview — Overview tab on detail page", async ({
    page,
  }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail("screenshots-projects-alpha");
    await portal.expectProjectDetailMounted();
    await captureScreenshot(page, "user-project-detail-overview");
  });
});

// ════════════════════════════════════════════════════════════════════
// scans (global queue)
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/scans", () => {
  let seed: SeedSummary | null = null;
  withSeedBeforeAll(
    "user-scans",
    ["screenshots-scans"],
    (s) => { seed = s; },
  );

  test.beforeEach(async ({ page }) => {
    if (seed === null) test.skip(true, "seed not available");
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed!.email, seed!.password);
  });

  test("user-scans-queue — global scan queue with seeded scan rows", async ({
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
  let seed: SeedSummary | null = null;
  withSeedBeforeAll(
    "user-components",
    ["screenshots-components"],
    (s) => { seed = s; },
  );

  test.beforeEach(async ({ page }) => {
    if (seed === null) test.skip(true, "seed not available");
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed!.email, seed!.password);
  });

  test("user-components-list — Components tab with virtualized rows", async ({
    page,
  }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail("screenshots-components");
    await portal.expectProjectDetailMounted();
    await portal.selectTab("components");
    await portal.expectComponentsTabReady();
    await captureScreenshot(page, "user-components-list");
  });

  test("user-licenses-donut — Licenses tab distribution", async ({ page }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail("screenshots-components");
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
  let seed: SeedSummary | null = null;
  withSeedBeforeAll(
    "user-vulns",
    ["screenshots-vulns"],
    (s) => { seed = s; },
  );

  test.beforeEach(async ({ page }) => {
    if (seed === null) test.skip(true, "seed not available");
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed!.email, seed!.password);
  });

  test("user-vulns-list — Vulnerabilities tab with seeded rows", async ({
    page,
  }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail("screenshots-vulns");
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
  let seed: SeedSummary | null = null;
  withSeedBeforeAll(
    "user-approvals",
    ["screenshots-approvals"],
    (s) => { seed = s; },
  );

  test.beforeEach(async ({ page }) => {
    if (seed === null) test.skip(true, "seed not available");
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed!.email, seed!.password);
  });

  test("user-approvals-inbox — approvals page mounted (empty until policy hits)", async ({
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
  let seed: SeedSummary | null = null;
  withSeedBeforeAll(
    "user-sbom",
    ["screenshots-sbom"],
    (s) => { seed = s; },
  );

  test.beforeEach(async ({ page }) => {
    if (seed === null) test.skip(true, "seed not available");
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed!.email, seed!.password);
  });

  test("user-sbom-tab — SBOM tab on the project detail page", async ({
    page,
  }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail("screenshots-sbom");
    await portal.expectProjectDetailMounted();
    await portal.selectSbomTab();
    await captureScreenshot(page, "user-sbom-tab");
  });
});

// ════════════════════════════════════════════════════════════════════
// obligations
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/obligations", () => {
  let seed: SeedSummary | null = null;
  withSeedBeforeAll(
    "user-obligations",
    ["screenshots-obligations"],
    (s) => { seed = s; },
  );

  test.beforeEach(async ({ page }) => {
    if (seed === null) test.skip(true, "seed not available");
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed!.email, seed!.password);
  });

  test("user-obligations-distribution — Obligations tab on the project detail page", async ({
    page,
  }) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail("screenshots-obligations");
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
  let seed: SeedSummary | null = null;
  withSeedBeforeAll(
    "user-notifications",
    ["screenshots-notifications"],
    (s) => { seed = s; },
  );

  test.beforeEach(async ({ page }) => {
    if (seed === null) test.skip(true, "seed not available");
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed!.email, seed!.password);
  });

  test("user-notifications-inbox — /notifications page mounted", async ({
    page,
  }) => {
    const notif = new NotificationsHarness(page);
    await notif.gotoNotifications();
    await captureScreenshot(page, "user-notifications-inbox");
  });

  test("user-notifications-prefs — preferences screen", async ({ page }) => {
    const notif = new NotificationsHarness(page);
    await notif.gotoPreferences();
    await captureScreenshot(page, "user-notifications-prefs");
  });
});

// ════════════════════════════════════════════════════════════════════
// integrations
// ════════════════════════════════════════════════════════════════════

test.describe.serial("@screenshots user-guide/integrations", () => {
  let seed: SeedSummary | null = null;
  withSeedBeforeAll(
    "user-integrations",
    ["screenshots-integrations"],
    (s) => { seed = s; },
  );

  test.beforeEach(async ({ page }) => {
    if (seed === null) test.skip(true, "seed not available");
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed!.email, seed!.password);
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
