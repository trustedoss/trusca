/**
 * Getting started checklist E2E (C2).
 *
 * The seed is what makes this worth running: it creates a project and a
 * succeeded scan, and creates neither a licence policy nor an API key. So a
 * seeded session lands on a checklist that is genuinely half done, and the
 * two ticks and the two blanks each have to come from a real server answer
 * rather than from a fixture written to agree with the component.
 *
 * The state this cannot reach is the empty organisation, because every seed
 * in CI has projects. That one was checked by hand against a freshly migrated
 * database, and is held by unit tests.
 *
 * Pre-requisites (auto-skip otherwise), identical to the other authenticated
 * specs:
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 + DATABASE_URL reachable for the seed script.
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "onboarding-smoke";

function tryAcquireSeed(
  testInfo: import("@playwright/test").TestInfo,
  opts: Parameters<typeof seedE2eUser>[0],
): SeedSummary | null {
  try {
    return seedE2eUser(opts);
  } catch (err) {
    testInfo.skip(
      true,
      `seed precondition failed - bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

async function signIn(page: import("@playwright/test").Page, seed: SeedSummary) {
  const auth = new AuthHarness(page);
  await auth.gotoLogin();
  await auth.login(seed.email, seed.password);
  const portal = new PortalPage(page);
  await portal.expectMounted();
  return portal;
}

test.describe("@onboarding getting started checklist", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("ticks what the seed did and leaves what it did not", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: [PROJECT_NAME],
      withScan: true,
    });
    if (seed === null) return;

    await page.setViewportSize({ width: 1280, height: 900 });
    await signIn(page, seed);

    const checklist = page.getByTestId("onboarding-checklist");
    await expect(checklist).toBeVisible();

    // The seed registers a project and runs a scan to success.
    await expect(page.getByTestId("onboarding-step-project")).toHaveAttribute(
      "data-done",
      "true",
    );
    await expect(page.getByTestId("onboarding-step-scan")).toHaveAttribute(
      "data-done",
      "true",
    );
    // It creates no licence policy and no API key. If a future seed starts
    // creating either, this fails rather than quietly asserting nothing.
    await expect(page.getByTestId("onboarding-step-policy")).toHaveAttribute(
      "data-done",
      "false",
    );
    await expect(page.getByTestId("onboarding-step-apiKey")).toHaveAttribute(
      "data-done",
      "false",
    );
    await expect(page.getByTestId("onboarding-progress")).toContainText("2");

    // A ticked step offers no button; the point of the card is what is left.
    await expect(page.getByTestId("onboarding-cta-project")).toHaveCount(0);
    await expect(page.getByTestId("onboarding-cta-scan")).toHaveCount(0);
  });

  test("its links reach the screens that finish the steps", async ({
    page,
  }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: [PROJECT_NAME],
      withScan: true,
    });
    if (seed === null) return;

    await page.setViewportSize({ width: 1280, height: 900 });
    await signIn(page, seed);

    await page.getByTestId("onboarding-cta-policy").click();
    await expect(page).toHaveURL(/\/policies$/);

    await page.goBack();
    await expect(page.getByTestId("onboarding-checklist")).toBeVisible();

    await page.getByTestId("onboarding-cta-apiKey").click();
    await expect(page).toHaveURL(/\/integrations$/);
  });

  test("dismissal survives a reload", async ({ page }, testInfo) => {
    const seed = tryAcquireSeed(testInfo, {
      projectNames: [PROJECT_NAME],
      withScan: true,
    });
    if (seed === null) return;

    await page.setViewportSize({ width: 1280, height: 900 });
    const portal = await signIn(page, seed);

    await page.getByTestId("onboarding-dismiss").click();
    await expect(page.getByTestId("onboarding-checklist")).toBeHidden();

    // The persistence is the point: an organisation that has been running for
    // a year should say "not for me" once, not once per visit.
    await page.reload();
    await portal.expectMounted();
    await expect(page.getByTestId("onboarding-checklist")).toBeHidden();
    // And the dashboard it leaves behind is the normal one, not a blank page.
    await expect(page.getByTestId("dashboard-kpi-grid")).toBeVisible();
  });
});
