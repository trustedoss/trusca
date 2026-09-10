/**
 * #425: one keyboard-only scenario per major flow (login, project
 * creation, approval workflow), as the issue asked for.
 *
 * "Keyboard-only" here means the same thing `tableSemantics.spec.ts`'s "S4"
 * test already means in this codebase: `.focus()` an element the way a
 * keyboard user's Tab sequence would land on it, assert it actually took
 * focus (a `div` with an `onClick` never does), then drive it with
 * `Enter`/`Space`/`Tab` alone, no `.click()`, no `.fill()`. `.fill()`
 * bypasses focus and dispatches input events directly; `page.keyboard.type`
 * does not, which is the difference this file exists to catch.
 *
 * Pre-requisites (auto-skip otherwise), as the other authenticated e2e specs:
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 *
 * Tagged `@keyboard-only`.
 */
import { expect, test } from "@playwright/test";

import { ApprovalsHarness } from "../_harness/ApprovalsHarness";
import { AuthHarness } from "../_harness/auth";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const BASE = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";
const PROJECT_DETAIL_URL = new RegExp(`/projects/[0-9a-f-]{36}$`);

function runNonce(): string {
  return Date.now().toString(36);
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
      `seed precondition failed: bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

/** The `data-testid` of whichever element currently holds DOM focus. */
async function focusedTestId(page: import("@playwright/test").Page): Promise<string> {
  return page.evaluate(() => {
    const el = document.activeElement;
    return el?.getAttribute("data-testid") ?? el?.tagName ?? "nothing";
  });
}

test.describe("@keyboard-only", () => {
  test.beforeEach(async ({ page }) => {
    await new AuthHarness(page).clearAuthState();
  });

  test("login: email, password and submit are each reachable by Tab and operable by keyboard alone", async ({
    page,
  }, testInfo) => {
    const nonce = runNonce();
    const seed = tryAcquireSeed(testInfo, {
      projectNames: [`kbd-login-${nonce}`],
    });
    if (!seed) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();

    // A fresh document, matching sidebar.spec.ts's skip-link test: a click
    // or an earlier form interaction would move Chromium's sequential focus
    // navigation point away from the top of the page.
    await page.reload();

    await page.getByTestId("login-email").focus();
    await expect(page.getByTestId("login-email")).toBeFocused();
    await page.keyboard.type(seed.email);

    await page.keyboard.press("Tab");
    await expect(page.getByTestId("login-password")).toBeFocused();
    await page.keyboard.type(seed.password);

    // password -> forgot-password link -> submit. Two Tabs, not one: the
    // link sits between them, and asserting the final landing spot (rather
    // than jumping straight to the submit testid) is what would notice a
    // control inserted ahead of it silently becoming unreachable.
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    expect(
      await focusedTestId(page),
      "Tab from the password field (via the forgot-password link) must land " +
        "on the submit button, or a keyboard-only user cannot submit the form",
    ).toBe("login-submit");

    await Promise.all([
      page.waitForURL(/\/(projects)?$/, { timeout: 10_000 }),
      page.keyboard.press("Enter"),
    ]);
    await auth.expectLoggedIn();
  });

  test("project creation: the name field and submit button are reachable and operable by keyboard alone", async ({
    page,
  }, testInfo) => {
    const nonce = runNonce();
    const seed = tryAcquireSeed(testInfo, {
      projectNames: [`kbd-create-seed-${nonce}`],
    });
    if (!seed) return;

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    await page.goto(`${BASE}/projects/new`);
    await page.getByTestId("project-name-input").focus();
    await expect(page.getByTestId("project-name-input")).toBeFocused();
    await page.keyboard.type(`kbd-created-${nonce}`);

    // name -> team combobox -> description -> git URL -> default branch ->
    // submit. Five Tabs from a required field, through four optional ones,
    // to the control that actually submits, the same shape as the login
    // assertion above, on a longer form.
    for (let i = 0; i < 5; i += 1) {
      await page.keyboard.press("Tab");
    }
    expect(
      await focusedTestId(page),
      "Tab from the project name field must reach the submit button after " +
        "the optional fields, or a keyboard-only user cannot create a project",
    ).toBe("project-create-submit");

    await Promise.all([
      page.waitForURL(PROJECT_DETAIL_URL, { timeout: 10_000 }),
      page.keyboard.press("Enter"),
    ]);
  });

  test("approval workflow: the transition-approve action is reachable and operable by keyboard alone", async ({
    page,
  }, testInfo) => {
    const nonce = runNonce();
    const seed = tryAcquireSeed(testInfo, {
      projectNames: [`kbd-approval-${nonce}`],
      withScan: true,
      componentCount: 3,
      componentPrefix: `kbdappr${nonce}`,
      vulnerabilityCount: 2,
      superAdmin: true,
      // A second admin: the requester may not decide their own request
      // (B3), so the approve control this test drives has to belong to a
      // different account than the one that created the request.
      extraMembers: 1,
      extraTeamAdmin: true,
    });
    if (!seed) return;

    const approver = seed.extra_members?.[0];
    expect(approver, "the seed did not produce a second team admin").toBeTruthy();
    const approverEmail = approver!.email;
    const approverPassword = seed.password;

    const approvals = new ApprovalsHarness(page);
    const requesterToken = await approvals.apiLogin(seed.email, seed.password);
    await approvals.apiSetApprovalRequiredStatuses(
      requesterToken,
      seed.team_id,
      ["suppressed"],
    );
    const findingId = await approvals.apiFirstFindingId(
      requesterToken,
      seed.project_ids[0],
    );
    await approvals.apiTransitionFinding(
      requesterToken,
      findingId,
      "analyzing",
      "starting triage on this finding",
    );
    const request = await approvals.apiRequestTransition(
      requesterToken,
      findingId,
      "suppressed",
      "accepted for this release, tracked in the risk register",
    );

    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(approverEmail, approverPassword);
    await approvals.gotoApprovals();
    await approvals.expectTransitionApproval(request.id);

    const approveButton = page.getByTestId(`transition-approve-${request.id}`);
    await approveButton.focus();
    await expect(
      approveButton,
      "the approve action must be a real, focusable control, or a keyboard " +
        "user reviewing the queue has no way to decide it",
    ).toBeFocused();
    await page.keyboard.press("Enter");

    await expect(
      page.getByTestId(`transition-approval-${request.id}`),
    ).toHaveCount(0, { timeout: 10_000 });

    const approverToken = await approvals.apiLogin(
      approverEmail,
      approverPassword,
    );
    await expect
      .poll(() => approvals.apiFindingStatus(approverToken, findingId))
      .toBe("suppressed");
  });
});
