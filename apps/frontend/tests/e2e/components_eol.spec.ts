/**
 * Components EOL E2E — Phase M (endoflife.date end-of-life flagging).
 *
 * Structural sibling of `vulnerabilities_kev.spec.ts` (sharedPage + seed-skip
 * conventions). The seed stamps EOL columns on exactly the FIRST seeded
 * component (`<prefix>-00000`: eol_state='eol', eol_date=2020-01-01 — a
 * far-past date that can never flip with the run date); every other
 * component stays NULL (untracked). Scenarios:
 *
 *   E1 — Exactly one row carries the EOL badge; absence is the signal for
 *        every untracked row (the EolBadge renders nothing for NULL /
 *        supported / unknown — the KevBadge contract).
 *   E2 — The toolbar's "EOL only" toggle narrows the list to the flagged
 *        row and mirrors `?eol=true` into the URL; toggling off restores
 *        the full list and drops the param.
 *   E3 — The flagged component's drawer shows the EOL row with the badge +
 *        inline date; an untracked component's drawer shows the "—" row.
 *   E4 — The Overview tab surfaces the EOL KPI chip with data-eol-count=1;
 *        its link deep-links into the Components tab pre-filtered.
 *
 * Selectors anchor on data-testid / data-eol-state / data-eol-count — never
 * translated copy — so the suite passes on EN and KO alike.
 */
import { type Page, expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-eol";
const COMPONENT_COUNT = 6;
const PREFIX = `eolseed${Date.now().toString(36)}`; // avoid cross-run purl collisions
const EOL_COMPONENT = `${PREFIX}-00000`;
const UNTRACKED_COMPONENT = `${PREFIX}-00001`;

let sharedPage: Page;
let seedFailed = false;
let seed: SeedSummary;

test.describe.serial("@components end-of-life badge + filter", () => {
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

  test("E1) exactly the seeded EOL row carries the badge", async () => {
    // All rows land in one virtualization window at COMPONENT_COUNT=6.
    await expect(
      sharedPage.getByTestId("component-row-cell-eol").first(),
    ).toBeVisible();
    const badges = sharedPage.getByTestId("eol-badge");
    await expect(badges).toHaveCount(1);
    await expect(badges.first()).toHaveAttribute("data-eol-state", "eol");
    await expect(badges.first()).toHaveAttribute(
      "data-eol-date",
      "2020-01-01",
    );
  });

  test("E2) the EOL-only toggle narrows the list and mirrors ?eol=true", async () => {
    const toggle = sharedPage.getByTestId("components-eol-filter");
    await toggle.click();
    await expect(toggle).toHaveAttribute("data-active", "true");
    await expect(sharedPage).toHaveURL(/eol=true/);

    const summary = sharedPage.getByTestId("components-summary");
    await expect(summary).toHaveAttribute("data-total", "1");
    await expect(
      sharedPage.getByText(EOL_COMPONENT, { exact: true }),
    ).toBeVisible();

    await toggle.click();
    await expect(toggle).toHaveAttribute("data-active", "false");
    await expect(sharedPage).not.toHaveURL(/eol=true/);
    await expect(summary).toHaveAttribute("data-total", String(COMPONENT_COUNT));
  });

  test("E3) drawer surfaces the EOL row (badge + date) and the untracked dash", async () => {
    const portal = new PortalPage(sharedPage);
    await portal.openComponentDrawer(EOL_COMPONENT);
    const eolRow = sharedPage.getByTestId("component-drawer-eol");
    await expect(eolRow).toBeVisible();
    await expect(eolRow.getByTestId("eol-badge")).toBeVisible();
    await expect(eolRow.getByTestId("eol-badge-date")).toBeVisible();
    await sharedPage.keyboard.press("Escape");

    await portal.openComponentDrawer(UNTRACKED_COMPONENT);
    const untrackedRow = sharedPage.getByTestId("component-drawer-eol");
    await expect(untrackedRow).toBeVisible();
    await expect(untrackedRow.getByTestId("eol-badge")).toHaveCount(0);
    await expect(untrackedRow).toContainText("—");
    await sharedPage.keyboard.press("Escape");
  });

  test("E4) overview KPI chip counts the EOL component and deep-links", async () => {
    const portal = new PortalPage(sharedPage);
    await portal.selectTab("overview");
    const chip = sharedPage.getByTestId("overview-eol-chip");
    await expect(chip).toBeVisible();
    await expect(chip).toHaveAttribute("data-eol-count", "1");

    await sharedPage.getByTestId("overview-eol-chip-link").click();
    await expect(sharedPage).toHaveURL(/tab=components/);
    await expect(sharedPage).toHaveURL(/eol=true/);
    await expect(
      sharedPage.getByTestId("components-eol-filter"),
    ).toHaveAttribute("data-active", "true");
  });
});
