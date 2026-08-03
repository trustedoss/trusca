/**
 * Known-malicious package E2E (#26) — sibling of `components_eol.spec.ts` and
 * `components_currency.spec.ts`.
 *
 * A malicious package is not a vulnerability: it was published to attack
 * whoever installs it, and the response is removal plus credential rotation
 * rather than an upgrade. The product therefore keeps it off the severity axis
 * and gives it its own badge, filter and Overview chip. This suite walks those
 * three surfaces.
 *
 * SEED NOTE — `apps/backend/scripts/seed_e2e_user.py` stamps the THIRD seeded
 * component (`-00002`) `malicious_state = "flagged"` and marks every other row
 * `clear`. Both halves are load-bearing: `clear` is a real verdict (the deny
 * list was consulted and did not list the package) and must render nothing,
 * while NULL would mean "never assessed". The seed writes the columns directly
 * rather than seeding a real malicious purl, so the fixture cannot rot when an
 * advisory is withdrawn upstream.
 *
 * Selectors anchor on data-testid / data-* attributes — never translated copy
 * — so the suite passes on EN and KO alike.
 */
import { type Page, expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-malicious";
const COMPONENT_COUNT = 6;
const PREFIX = `malseed${Date.now().toString(36)}`; // avoid cross-run purl collisions
const FLAGGED_ROW = `${PREFIX}-00002`;

let sharedPage: Page;
let seedFailed = false;
let seed: SeedSummary;

test.describe.serial("@components known-malicious badge + filter", () => {
  test.beforeAll(async ({ browser }) => {
    sharedPage = await browser.newPage();
    try {
      seed = seedE2eUser({
        projectNames: [PROJECT_NAME],
        withScan: true,
        componentCount: COMPONENT_COUNT,
        componentPrefix: PREFIX,
      });
    } catch {
      seedFailed = true;
      return;
    }
    const auth = new AuthHarness(sharedPage);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
  });

  test.afterAll(async () => {
    await sharedPage?.close();
  });

  test.beforeEach(async () => {
    test.skip(
      seedFailed,
      "seed precondition failed — bring docker-compose dev up + ensure python3 is on PATH",
    );
  });

  test("M1) exactly the seeded flagged row carries the malicious badge", async () => {
    const portal = new PortalPage(sharedPage);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectTab("components");

    await expect(
      sharedPage.getByTestId("components-header-cell-malicious"),
    ).toBeVisible();

    // One badge, on the row the fixture marked. The other five are `clear`,
    // which renders nothing — absence is the signal on a dense row.
    await expect(sharedPage.getByTestId("malicious-badge")).toHaveCount(1);
    const flaggedRow = sharedPage
      .getByTestId("component-row")
      .filter({ hasText: FLAGGED_ROW });
    await expect(flaggedRow.getByTestId("malicious-badge")).toHaveCount(1);
  });

  test("M2) the Flagged-only toggle mirrors ?malicious=true and narrows the list", async () => {
    const toggle = sharedPage.getByTestId("components-malicious-filter");
    await toggle.click();
    await expect(toggle).toHaveAttribute("data-active", "true");
    await expect(sharedPage).toHaveURL(/malicious=true/);

    const summary = sharedPage.getByTestId("components-summary");
    await expect(summary).toHaveAttribute("data-total", "1");

    await toggle.click();
    await expect(toggle).toHaveAttribute("data-active", "false");
    await expect(sharedPage).not.toHaveURL(/malicious=true/);
    await expect(summary).toHaveAttribute(
      "data-total",
      String(COMPONENT_COUNT),
    );
  });

  test("M3) the drawer names the advisory and states the response", async () => {
    const portal = new PortalPage(sharedPage);
    await portal.openComponentDrawer(FLAGGED_ROW);

    const block = sharedPage.getByTestId("component-drawer-malicious");
    await expect(block).toBeVisible();
    expect(await portal.getDrawerMaliciousState()).toBe("flagged");
    await expect(
      block.getByTestId("malicious-badge-advisory"),
    ).toBeVisible();

    await sharedPage.keyboard.press("Escape");
  });

  test("M4) an ordinary component's drawer carries no malicious block", async () => {
    const portal = new PortalPage(sharedPage);
    // `-00003` is assessed-and-clean. The block is absent rather than showing
    // "—" (unlike the EOL/currency rows), so the reader's eye is not trained
    // to skip it.
    await portal.openComponentDrawer(`${PREFIX}-00003`);
    await expect(
      sharedPage.getByTestId("component-drawer-malicious"),
    ).toHaveCount(0);
    expect(await portal.getDrawerMaliciousState()).toBeNull();

    await sharedPage.keyboard.press("Escape");
  });

  test("M5) the Overview chip counts the flagged row and deep-links to it", async () => {
    const portal = new PortalPage(sharedPage);
    await portal.selectTab("overview");

    const chip = sharedPage.getByTestId("overview-malicious-chip");
    await expect(chip).toBeVisible();
    await expect(chip).toHaveAttribute("data-malicious-count", "1");

    await sharedPage.getByTestId("overview-malicious-chip-link").click();

    // Lands on the Components tab pre-filtered, so the count is actionable
    // rather than something to go hunt for.
    await expect(sharedPage).toHaveURL(/tab=components/);
    await expect(sharedPage).toHaveURL(/malicious=true/);
    await expect(
      sharedPage.getByTestId("components-summary"),
    ).toHaveAttribute("data-total", "1");
  });
});
