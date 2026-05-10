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
  captureSection,
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
    await captureSection(
      page,
      "user-profile-connected-accounts",
      "profile-connected-accounts",
    );
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

  // Switch to the Succeeded tab so the seeded scan row is visible — the
  // shared seed (`global-setup.ts` with `withScan: true`) creates a
  // succeeded scan, while the page defaults to the Running tab which
  // would render the empty-state card. Both tabs are valid captures, but
  // a populated row is the more useful guide asset.
  test("user-scans-queue — global scan queue with seeded scan rows", async ({
    page,
  }) => {
    const scans = new ScansQueueHarness(page);
    await scans.gotoScans();
    await scans.selectTab("succeeded");
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

  // The empty inbox is itself a valid guide asset — most fresh
  // installations land on this view until the first policy hit. The
  // harness mount predicate now keys on the always-rendered table +
  // pagination wrappers (rather than the empty-tbody zero-height race
  // the original `tbody OR empty-cell` fallback hit) so the capture
  // settles deterministically.
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

  // The preferences section sits below the inbox on the same page; the
  // harness verb scrolls it into view and waits for the prefs query to
  // settle (form / loading / error — any terminal state is enough for
  // the capture, downstream interactive verbs still wait on the form
  // testid before toggling).
  test("user-notifications-prefs — preferences screen", async ({ page }) => {
    const notif = new NotificationsHarness(page);
    await notif.gotoNotifications();
    await notif.gotoPreferences();
    await captureSection(
      page,
      "user-notifications-prefs",
      "notifications-prefs-section",
    );
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

  test("user-integrations-webhooks — Webhooks section with GitHub + GitLab cards", async ({
    page,
  }) => {
    const integrations = new IntegrationsHarness(page);
    await integrations.goto();
    await integrations.expectMounted();
    await integrations.scrollToWebhooks();
    await captureSection(
      page,
      "user-integrations-webhooks",
      "integrations-webhooks-section",
    );
  });
});
