/**
 * Integrations E2E — chore C smoke coverage.
 *
 * Drives the `/integrations` route against the docker-compose dev stack to
 * verify the page mounts for an authenticated developer and that the
 * Create API Key dialog opens / closes via the keyboard a11y path.
 *
 * Scope is intentionally limited:
 *   - We do NOT actually create a key here. Create + revoke round trip is
 *     covered by the unit tests; running it in E2E adds Postgres write
 *     flakiness for negligible additional signal.
 *   - We do NOT exercise webhook copy buttons (clipboard is browser-gated
 *     and varies between headless modes).
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 *
 * Selectors live in `tests/_harness/integrations.ts` and `tests/_harness/auth.ts`
 * — every assertion is rooted in `data-testid`, never a translated string.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { IntegrationsHarness } from "../_harness/integrations";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "integrations-smoke";

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

test.describe("@integrations api keys + webhooks page", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("integrations page mounts and the create dialog opens then closes", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: [PROJECT_NAME],
    });
    if (seed === null) return;

    // Sign in as the seeded developer-role user. The seed creates a fresh
    // team + a single project; that's enough to satisfy /v1/api-keys list
    // (it will simply return zero items).
    const auth = new AuthHarness(page);
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const integrations = new IntegrationsHarness(page);
    await integrations.goto();
    await integrations.expectMounted();

    // Open dialog via the harness verb.
    await integrations.clickCreate();
    await integrations.expectCreateDialogOpen();

    // The interactive controls inside the dialog must hydrate — we don't
    // submit, but we sanity-check that the form is reachable so a future
    // regression that mounts the dialog frame without its body would fail.
    await expect(page.getByTestId("integrations-create-form")).toBeVisible();
    await expect(page.getByTestId("integrations-create-name")).toBeVisible();

    // Close via Escape (canonical shadcn dialog affordance) — verifies the
    // keyboard path stays wired and the dialog unmounts cleanly.
    await integrations.closeCreateDialog();
  });
});
