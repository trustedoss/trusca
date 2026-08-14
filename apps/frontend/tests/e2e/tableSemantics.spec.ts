/**
 * G0-5 backlog — the project-detail tables are tables.
 *
 * Why this exists
 * ---------------
 * Both tables are built from flex `div`s, not `<table>`, because their bodies
 * are virtualized. For a long time that meant they had no table semantics at
 * all: a screen reader met a flat run of buttons, could not move by column,
 * and `aria-sort` had nowhere valid to live — which is how it ended up on a
 * `<button>`, where it is invalid ARIA that assistive technology ignores.
 *
 * The roles are now wired by hand, and hand-wired roles are exactly the kind
 * of thing that rots: they are invisible in the rendered page, no unit test
 * touches them, and the axe gate only proves that what is there is *legal* —
 * `aria-required-children` passes on a table with one row as happily as on a
 * complete one. This asserts the structure is actually there and actually
 * connected.
 *
 * The subtle part it guards is the ownership chain. Virtuoso puts a wrapper, a
 * scroller, a viewport, a list and a per-item box between the table and its
 * rows. Plain `div`s flatten out of the accessibility tree, so they are
 * harmless — except the scroller, which carries `tabindex` so the list can be
 * scrolled from the keyboard, and a focusable element is never flattened. It
 * therefore has to hold a role the table allows, and it holds `rowgroup`. Drop
 * that one prop and the rows stop being owned by the table; nothing else in
 * the suite would notice.
 */
import { type Page, expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-table-semantics";
const COMPONENT_COUNT = 6;
const VULNERABILITY_COUNT = 4;
const COMPONENT_PREFIX = `semseed${Date.now().toString(36)}`;

/**
 * Walk from a row up to the nearest ancestor carrying `role="table"`, listing
 * every role met on the way.
 *
 * The accessibility tree only owns a row through elements that are either
 * flattened (no role) or legal owners (`rowgroup`). Returning the chain rather
 * than a boolean means a failure names the element that broke it.
 */
async function ownershipChain(page: Page, rowTestId: string): Promise<string[]> {
  return page.evaluate((testId) => {
    const row = document.querySelector<HTMLElement>(
      `[data-testid="${testId}"]`,
    );
    if (!row) throw new Error(`no row: ${testId}`);
    const chain: string[] = [];
    for (let el = row.parentElement; el; el = el.parentElement) {
      const role = el.getAttribute("role");
      const focusable = el.hasAttribute("tabindex");
      chain.push(role ?? (focusable ? "(focusable, no role)" : "(generic)"));
      if (role === "table") break;
    }
    return chain;
  }, rowTestId);
}

let sharedPage: Page;
let seedFailed = false;
let seed: SeedSummary;

test.describe.serial("@tables the project-detail tables expose table semantics", () => {
  test.beforeAll(async ({ browser }) => {
    sharedPage = await browser.newPage();
    try {
      seed = seedE2eUser({
        projectNames: [PROJECT_NAME],
        withScan: true,
        componentCount: COMPONENT_COUNT,
        componentPrefix: COMPONENT_PREFIX,
        vulnerabilityCount: VULNERABILITY_COUNT,
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
  });

  test("S1) the Vulnerabilities table announces rows, columns and sort", async () => {
    const portal = new PortalPage(sharedPage);
    await portal.selectTab("vulnerabilities");
    await portal.expectVulnerabilitiesTabReady();

    const table = sharedPage.getByRole("table");
    await expect(table).toHaveCount(1);

    // The count is the RESULT SET, not the rendered window — that is the whole
    // point of the attribute under virtualization. +1 for the header row.
    const rowcount = Number(await table.getAttribute("aria-rowcount"));
    const loaded = Number(
      await sharedPage
        .getByTestId("vulnerabilities-virtual")
        .getAttribute("data-total"),
    );
    expect(rowcount, "aria-rowcount counts the header plus every finding").toBe(
      loaded + 1,
    );

    // Column headers, not a row of anonymous text.
    const headers = table.getByRole("columnheader");
    expect(await headers.count()).toBeGreaterThanOrEqual(8);

    // Cells, so a screen reader can move across a row.
    const firstRow = sharedPage.getByTestId("vulnerability-row").first();
    await expect(firstRow).toHaveAttribute("role", "row");
    await expect(firstRow).toHaveAttribute("aria-rowindex", "2");
    expect(await firstRow.getByRole("cell").count()).toBeGreaterThanOrEqual(8);

    // Nothing between the row and the table may be visible-but-not-a-rowgroup.
    // A focusable div with no role is the failure mode this guards: Virtuoso's
    // scroller is focusable, so it must carry `rowgroup`.
    const chain = await ownershipChain(sharedPage, "vulnerability-row");
    expect(
      chain.filter((r) => r !== "(generic)" && r !== "rowgroup"),
      `the chain from the row up to the table was ${chain.join(" -> ")}; ` +
        `only generic (flattened) elements and rowgroups may appear in it`,
    ).toEqual(["table"]);
    expect(chain, "a rowgroup has to own the rows").toContain("rowgroup");
  });

  test("S2) aria-sort lives on the column header and follows the sort", async () => {
    const portal = new PortalPage(sharedPage);
    await portal.selectTab("vulnerabilities");
    await portal.expectVulnerabilitiesTabReady();

    const severityHeader = sharedPage.getByTestId(
      "vulnerabilities-header-cell-status",
    );
    await expect(severityHeader).toHaveAttribute("role", "columnheader");
    // "none", never absent: on a sortable column the missing attribute reads
    // as "this column cannot be sorted".
    await expect(severityHeader).toHaveAttribute("aria-sort", "none");

    await sharedPage.getByTestId("vulnerabilities-sort-header-status").click();
    await expect(severityHeader).toHaveAttribute("aria-sort", "ascending");
    await sharedPage.getByTestId("vulnerabilities-sort-header-status").click();
    await expect(severityHeader).toHaveAttribute("aria-sort", "descending");

    // And it is not back on the button, where it was invalid and inert.
    await expect(
      sharedPage.getByTestId("vulnerabilities-sort-header-status"),
    ).not.toHaveAttribute("aria-sort");
  });

  test("S3) the Components table exposes the same structure", async () => {
    const portal = new PortalPage(sharedPage);
    await portal.selectTab("components");
    await portal.expectComponentsTabReady();

    const table = sharedPage.getByRole("table");
    await expect(table).toHaveCount(1);
    expect(await table.getByRole("columnheader").count()).toBeGreaterThanOrEqual(8);

    const firstRow = sharedPage.getByTestId("component-row").first();
    await expect(firstRow).toHaveAttribute("role", "row");
    await expect(firstRow).toHaveAttribute("aria-rowindex", "2");
    expect(await firstRow.getByRole("cell").count()).toBeGreaterThanOrEqual(8);

    const chain = await ownershipChain(sharedPage, "component-row");
    expect(
      chain.filter((r) => r !== "(generic)" && r !== "rowgroup"),
      `the chain from the row up to the table was ${chain.join(" -> ")}`,
    ).toEqual(["table"]);
  });

  test("S5) the Compliance grid announces rows and columns", async () => {
    // G0-5 gave the components and vulnerabilities grids their semantics and
    // left this one out, so it announced an unlabelled stack of divs.
    const portal = new PortalPage(sharedPage);
    await portal.selectTab("compliance");
    await sharedPage
      .getByTestId("compliance-virtual")
      .or(sharedPage.getByTestId("compliance-empty"))
      .waitFor({ timeout: 15_000 });
    test.skip(
      (await sharedPage.getByTestId("compliance-virtual").count()) === 0,
      "seed produced no licence findings",
    );

    const table = sharedPage.getByRole("table");
    await expect(table).toHaveCount(1);

    const rowcount = Number(await table.getAttribute("aria-rowcount"));
    const total = Number(
      await sharedPage
        .getByTestId("compliance-virtual")
        .getAttribute("data-total"),
    );
    expect(rowcount, "aria-rowcount counts the header plus every licence").toBe(
      total + 1,
    );
    expect(
      await table.getByRole("columnheader").count(),
    ).toBeGreaterThanOrEqual(5);

    const firstRow = sharedPage.getByTestId("compliance-row").first();
    await expect(firstRow).toHaveAttribute("role", "row");
    await expect(firstRow).toHaveAttribute("aria-rowindex", "2");

    const chain = await ownershipChain(sharedPage, "compliance-row");
    expect(
      chain.filter((r) => r !== "(generic)" && r !== "rowgroup"),
      `the chain from the row up to the table was ${chain.join(" -> ")}`,
    ).toEqual(["table"]);
  });

  test("S6) Back closes the licence drawer instead of leaving the tab", async () => {
    const portal = new PortalPage(sharedPage);
    await portal.selectTab("compliance");
    await sharedPage
      .getByTestId("compliance-virtual")
      .or(sharedPage.getByTestId("compliance-empty"))
      .waitFor({ timeout: 15_000 });
    test.skip(
      (await sharedPage.getByTestId("compliance-virtual").count()) === 0,
      "seed produced no licence findings",
    );

    await sharedPage.getByTestId("compliance-row").first().click();
    await expect(sharedPage.getByTestId("license-drawer")).toBeVisible({
      timeout: 10_000,
    });
    expect(sharedPage.url()).toContain("license=");

    await sharedPage.goBack();

    await expect(sharedPage.getByTestId("license-drawer")).toBeHidden();
    expect(sharedPage.url()).not.toContain("license=");
    await expect(sharedPage.getByTestId("compliance-virtual")).toBeVisible();
  });

  test("S4) the drawer is still reachable by keyboard alone", async () => {
    // The click target moved out of a nine-cell button and into one control in
    // the identifier cell. That is only an improvement if the control is still
    // a real button a keyboard can reach and activate.
    const portal = new PortalPage(sharedPage);
    await portal.selectTab("vulnerabilities");
    await portal.expectVulnerabilitiesTabReady();

    const open = sharedPage.getByTestId("vulnerability-row-open").first();
    await open.focus();
    await expect(open).toBeFocused();
    await sharedPage.keyboard.press("Enter");
    await expect(sharedPage.getByTestId("vulnerability-drawer")).toBeVisible({
      timeout: 10_000,
    });
  });
});
