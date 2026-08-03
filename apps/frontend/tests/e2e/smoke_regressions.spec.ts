/**
 * Smoke / regression guard — 2026-05-25 manual-walkthrough findings.
 *
 * Codifies bugs found by driving the running app by hand that the unit suite
 * could not catch (they only surface with a real browser + real backend). Each
 * `test` maps to a fixed defect so a reappearance fails CI loudly:
 *
 *   - #16/#17  Project creation via the UI 422'd forever (/auth/me returned no
 *              team_id) AND a fresh signup had no team at all. Guarded by the
 *              full "register → team → create project" funnel.
 *   - "Register project" buttons (empty-state CTA + header) were inert.
 *   - Password floor lowered to 8 (NIST 800-63B minimum).
 *   - #12  `/` now redirects to `/projects` (dashboard dropped).
 *   - #10  The language toggle shows the *current* language and <html lang>
 *          tracks the active language.
 *
 * Auth strategy: every test authenticates by REGISTERING a fresh user
 * (`/auth/register`, which now auto-provisions a personal team). Registration
 * still auto-logs-in via `/auth/login`, so — like the rest of the e2e suite —
 * run the backend with `RATELIMIT_DISABLED=1` (single egress IP otherwise
 * trips the 5/min login limiter across a multi-test run). See
 * `apps/backend/core/ratelimit.py` + the `RATELIMIT_DISABLED` wiring in
 * `docker-compose.dev.yml`. Example:
 *   RATELIMIT_DISABLED=1 docker-compose -f docker-compose.dev.yml up -d backend
 *   npm run test:smoke
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";

const BASE = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";
const PROJECT_DETAIL_URL = new RegExp(`/projects/[0-9a-f-]{36}$`);

/** Register a fresh user (auto-logs-in, lands on `/`). Returns the harness. */
async function freshUser(page: import("@playwright/test").Page): Promise<AuthHarness> {
  const auth = new AuthHarness(page);
  await auth.gotoRegister();
  await auth.register({
    email: auth.randomEmail(),
    password: "abcdefg8", // exactly 8 — also guards the lowered floor
    displayName: "Smoke",
  });
  return auth;
}

/** Register a fresh user + create a project; leaves the page on its detail. */
async function freshUserWithProject(
  page: import("@playwright/test").Page,
): Promise<void> {
  await freshUser(page);
  await page.goto(`${BASE}/projects/new`);
  await page.getByTestId("project-name-input").fill(`smoke-${Date.now()}`);
  await page.getByTestId("project-create-submit").click();
  await expect(page).toHaveURL(PROJECT_DETAIL_URL, { timeout: 10_000 });
}

test.describe("@smoke regression guards (manual-walkthrough 2026-05-25)", () => {
  test.beforeEach(async ({ page }) => {
    await new AuthHarness(page).clearAuthState();
  });

  test("#16/#17 fresh signup gets a team and creates a project via the header button", async ({
    page,
  }) => {
    // Full funnel: register (auto-provisions a personal team) → open the create
    // form via the always-present header "Register project" button → submit →
    // land on the new project's detail page. Pre-fix this 422'd (no team_id) or
    // blocked with a "no team" notice, and the header button was inert.
    await freshUser(page);

    await page.goto(`${BASE}/projects`);
    await page.getByTestId("project-list-register").click();
    await expect(page).toHaveURL(`${BASE}/projects/new`);

    await expect(page.getByTestId("project-create-no-team")).toHaveCount(0);
    await page.getByTestId("project-name-input").fill(`smoke-${Date.now()}`);
    await page
      .getByTestId("project-git-url-input")
      .fill("https://github.com/pallets/flask");
    await page.getByTestId("project-create-submit").click();

    await expect(page).toHaveURL(PROJECT_DETAIL_URL, { timeout: 10_000 });
    await expect(page.getByTestId("project-create-error")).toHaveCount(0);
  });

  test("#18A the scan dialog exposes an optional release/version input", async ({
    page,
  }) => {
    await freshUserWithProject(page);
    await page.goto(`${BASE}/projects`);
    await page.getByTestId("project-row-scan").first().click();
    await expect(page.getByTestId("source-select-dialog")).toBeVisible();
    await expect(page.getByTestId("scan-release-input")).toBeVisible();
  });

  test("a developer can start a scan from the project DETAIL page (no bounce to the list)", async ({
    page,
  }) => {
    // UX fix: the scan trigger used to live only on the project list, so after
    // creating a project (which lands you on the detail page) there was no way
    // to scan without navigating back. Assert the detail header has it.
    await freshUserWithProject(page);
    await expect(page.getByTestId("project-detail-scan")).toBeVisible();
    await page.getByTestId("project-detail-scan").click();
    await expect(page.getByTestId("source-select-dialog")).toBeVisible();
    await expect(page.getByTestId("scan-release-input")).toBeVisible();
  });

  test("New Project explains that scanning happens after creation", async ({
    page,
  }) => {
    await freshUser(page);
    await page.goto(`${BASE}/projects/new`);
    await expect(page.getByTestId("project-create-scan-hint")).toBeVisible();
  });

  test("#25 project rows show a real scan-status badge (not a hardcoded Idle)", async ({
    page,
  }) => {
    // A freshly-created (never-scanned) project renders the status badge driven
    // by latest_scan_status — it reads "Idle" via the badge, not a hardcoded
    // string. (Populated status/severity needs a scan; covered by unit tests.)
    await freshUserWithProject(page);
    await page.goto(`${BASE}/projects`);
    await expect(page.getByTestId("project-row-status").first()).toBeVisible();
  });

  test("#28 the Releases tab renders (empty state for a never-scanned project)", async ({
    page,
  }) => {
    // A freshly-created project has no succeeded scan → the Releases tab shows
    // its empty state. (The populated table + historical read-only banner are
    // covered by unit tests; this guards the tab is wired into the detail page.)
    await freshUserWithProject(page);
    await page.getByTestId("project-detail-tab-releases").click();
    await expect(page.getByTestId("releases-tab")).toBeVisible();
    await expect(page.getByTestId("releases-empty")).toBeVisible();
  });

  test("#28 the compare route mounts (missing-params prompt for a no-scan project)", async ({
    page,
  }) => {
    // Routing guard for the version-compare view. A fresh project has <2
    // releases, so the page renders its missing-params prompt; the populated
    // diff + summary deltas are covered by unit tests.
    await freshUserWithProject(page); // lands on /projects/<id>
    const projectUrl = page.url();
    await page.goto(`${projectUrl}/compare`);
    await expect(page.getByTestId("compare-page")).toBeVisible();
  });

  test("the global Scans page opens on the All tab (not the empty Running tab)", async ({
    page,
  }) => {
    await freshUser(page);
    await page.goto(`${BASE}/scans`);
    await expect(page.getByTestId("scans-tab-all")).toHaveAttribute(
      "data-active",
      "true",
    );
  });

  test("#18B set + remove a private-repo git credential in project Settings", async ({
    page,
  }) => {
    await freshUserWithProject(page);
    // Land on the new project's detail → open the Settings tab.
    await page.getByTestId("project-detail-tab-settings").click();
    await expect(page.getByTestId("settings-git-credential-section")).toBeVisible();

    // Empty → set a credential. The plaintext is write-only; the UI must flip
    // to a "configured" state (has_git_credential=true) without echoing it.
    await page
      .getByTestId("project-git-credential-input")
      .fill("ghp_smoketoken1234567890");
    await page.getByTestId("project-git-credential-save").click();
    await expect(page.getByTestId("project-git-credential-configured")).toBeVisible({
      timeout: 10_000,
    });

    // Remove → back to the empty input state.
    await page.getByTestId("project-git-credential-remove").click();
    await expect(page.getByTestId("project-git-credential-input")).toBeVisible({
      timeout: 10_000,
    });
  });

  test("the empty-state 'Register project' CTA navigates to the create form", async ({
    page,
  }) => {
    // A brand-new user has no projects → the empty-state CTA renders. It was a
    // plain <button> with no handler; assert it now routes to /projects/new.
    await freshUser(page);

    await page.goto(`${BASE}/projects`);
    const cta = page.getByTestId("project-list-empty-cta");
    await expect(cta).toBeVisible();
    await cta.click();
    await expect(page).toHaveURL(`${BASE}/projects/new`);
  });

  test("registration accepts an 8-character password (NIST 800-63B floor)", async ({
    page,
  }) => {
    // freshUser registers with an exactly-8-char password; if the floor
    // regressed to 12 the register submit would never reach /.
    const auth = await freshUser(page);
    await auth.expectLoggedIn();
  });

  test("#12 the / root lands on the dashboard (reintroduced 2026-05-25)", async ({
    page,
  }) => {
    // User-test 2026-05-25 reverted the earlier "drop the dashboard" call:
    // Dashboard now lives at `/` again with the cross-portal risk portfolio,
    // and the sidebar `nav-dashboard` link points back to it. Older versions
    // of this test asserted the inverse contract (`/` → `/projects`, no
    // dashboard nav); the assertions below pin the current behaviour.
    await freshUser(page);

    await page.goto(`${BASE}/`);
    await expect(page).toHaveURL(`${BASE}/`);
    await expect(page.getByTestId("nav-dashboard")).toBeVisible({
      timeout: 10_000,
    });
  });

  test("#10 language toggle shows the CURRENT language and syncs <html lang>", async ({
    page,
  }) => {
    await freshUser(page);

    const toggle = page.getByTestId("language-toggle");
    await expect(toggle).toBeVisible();

    const readState = () =>
      page.evaluate(() => ({
        current: document
          .querySelector('[data-testid="language-toggle"]')
          ?.getAttribute("data-current-language"),
        label: document
          .querySelector('[data-testid="language-toggle"]')
          ?.textContent?.trim(),
        htmlLang: document.documentElement.lang,
      }));

    // <html lang> must track the active language (a11y/SEO regression guard).
    const before = await readState();
    expect(before.htmlLang).toBe(before.current);

    // Land in Korean and assert the toggle shows the CURRENT (Korean) name —
    // the pre-fix bug showed the *target* ("English") while in Korean.
    if (before.current !== "ko") {
      await toggle.click();
    } else {
      await toggle.click(); // → en
      await toggle.click(); // → ko
    }
    await expect.poll(async () => (await readState()).current).toBe("ko");
    const ko = await readState();
    expect(ko.htmlLang).toBe("ko");
    expect(ko.label).toContain("한국어");
  });
});
