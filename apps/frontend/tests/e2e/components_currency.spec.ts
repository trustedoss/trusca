/**
 * Components version-currency E2E — the lower-urgency sibling of
 * `components_eol.spec.ts`.
 *
 * Version currency answers "is this version behind the newest patch of its
 * release line?" (`currency_state === "outdated"`). It mirrors the EOL
 * surfaces exactly: a CurrencyBadge in the table + drawer, and an
 * "Outdated only" toolbar toggle that drives `?outdated=true`.
 *
 * SEED NOTE — the e2e seed (`apps/backend/scripts/seed_e2e_user.py`) stamps
 * the EOL columns on the first component but does NOT stamp the currency
 * columns (currency_state / currency_latest stay NULL for every seeded row).
 * That is a backend seed file this suite may not edit, so we do not fight it:
 * no seeded component is `outdated`. This spec therefore exercises the
 * seed-independent contract — the toggle flips `data-active`, mirrors
 * `?outdated=true` into the URL, and narrows the list to zero rows (nothing
 * is outdated) — and defers badge-presence / drawer-latest-patch coverage to
 * the unit tests (`CurrencyBadge.test.tsx`, `ComponentDetailBody.test.tsx`),
 * which drive real `outdated` fixtures directly.
 *
 * Selectors anchor on data-testid / data-active — never translated copy — so
 * the suite passes on EN and KO alike.
 */
import { type Page, expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-currency";
const COMPONENT_COUNT = 6;
const PREFIX = `curseed${Date.now().toString(36)}`; // avoid cross-run purl collisions

let sharedPage: Page;
let seedFailed = false;
let seed: SeedSummary;

test.describe.serial("@components version-currency badge + filter", () => {
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
    const portal = new PortalPage(sharedPage);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectTab("components");
  });

  test("C1) no seeded component carries the currency badge (absence is the signal)", async () => {
    // All rows land in one virtualization window at COMPONENT_COUNT=6. The
    // seed leaves currency columns NULL, so the CurrencyBadge renders nowhere.
    await expect(
      sharedPage.getByTestId("component-row-cell-currency").first(),
    ).toBeVisible();
    await expect(sharedPage.getByTestId("currency-badge")).toHaveCount(0);
  });

  test("C2) the Outdated-only toggle mirrors ?outdated=true and narrows the list", async () => {
    const toggle = sharedPage.getByTestId("components-outdated-filter");
    await toggle.click();
    await expect(toggle).toHaveAttribute("data-active", "true");
    await expect(sharedPage).toHaveURL(/outdated=true/);

    // Nothing is outdated in the seed → the filtered list is empty.
    const summary = sharedPage.getByTestId("components-summary");
    await expect(summary).toHaveAttribute("data-total", "0");

    await toggle.click();
    await expect(toggle).toHaveAttribute("data-active", "false");
    await expect(sharedPage).not.toHaveURL(/outdated=true/);
    await expect(summary).toHaveAttribute(
      "data-total",
      String(COMPONENT_COUNT),
    );
  });

  test("C3) an untracked component's drawer shows the '—' currency row", async () => {
    const portal = new PortalPage(sharedPage);
    await portal.openComponentDrawer(`${PREFIX}-00001`);
    const row = sharedPage.getByTestId("component-drawer-currency");
    await expect(row).toBeVisible();
    await expect(row.getByTestId("currency-badge")).toHaveCount(0);
    await expect(row).toContainText("—");
    // The harness reader agrees the row carries no outdated verdict.
    expect(await portal.getDrawerCurrencyState()).toBeNull();
    await sharedPage.keyboard.press("Escape");
  });
});
