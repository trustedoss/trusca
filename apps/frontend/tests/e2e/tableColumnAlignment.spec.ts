/**
 * G0-5 — the column headers must sit above their own data.
 *
 * Why this exists
 * ---------------
 * Both project-detail tables render their header and their rows as two
 * independent flex rows that agree on column widths only by convention. The
 * convention had broken on both of them, and nothing was checking:
 *
 *   Vulnerabilities  the track floor said 1250 px while the declared columns
 *                    summed to 1596. The header, being an ordinary flex row,
 *                    absorbed the 346 px shortfall by shrinking its cells. The
 *                    rows would not: their inner button had `min-width: auto`,
 *                    so it sized to the untruncated text and overflowed the
 *                    track instead. By the last column the two were 327 px
 *                    apart — "Discovered" stood above the SLA dates.
 *   Components       same shape, floor 820 px against 1120 px of columns,
 *                    321 px of row overflow.
 *
 * Both were visible in the committed 1440 px visual baselines, and both had
 * been approved by eye. That is the point: a screenshot review answers "does
 * this look like the product" and not "is CVSS above the CVSS numbers", and
 * the arithmetic is not something a reviewer can be asked to do per column.
 *
 * What it asserts
 * ---------------
 * For each table, the header's cells and a row's cells are flattened to one
 * list each — the row's inner "open" button is spliced in place so both lists
 * are the same sequence of columns — and then compared position by position.
 * Same count, same left edge, same width, within a pixel.
 *
 * It is deliberately column-agnostic. Adding a column to one of these tables
 * without raising the track floor makes this fail, which is precisely what
 * happened four times (Title, Discovered, SLA due, and the Components set)
 * with nothing to notice it.
 */
import { type Page, expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-column-alignment";
const COMPONENT_COUNT = 6;
const VULNERABILITY_COUNT = 4;
/** Unique per run — `purl` is globally unique, so a fixed prefix collides. */
const COMPONENT_PREFIX = `alignseed${Date.now().toString(36)}`;

/** Cell geometry, as the browser laid it out. */
interface Cell {
  testid: string;
  left: number;
  width: number;
  text: string;
}

interface TableGeometry {
  header: Cell[];
  row: Cell[];
  headerClientWidth: number;
  headerScrollWidth: number;
  rowClientWidth: number;
  rowScrollWidth: number;
}

/**
 * Read the header cells and the first row's cells as two comparable lists.
 *
 * Both are flat now: the row's cells are its own children, because the button
 * that used to wrap nine of them was removed when the tables were given table
 * semantics (a `row` owns `cell`s, and a button spanning nine of them is not
 * one). The splice below is kept for the shape, and asserts that: if a wrapper
 * ever comes back, this still lines the columns up rather than comparing each
 * against its neighbour — but `aria-required-children` would fail first.
 */
async function readTableGeometry(
  page: Page,
  headerTestId: string,
  rowTestId: string,
  openButtonTestId: string,
): Promise<TableGeometry> {
  return page.evaluate(
    ({ headerTestId, rowTestId, openButtonTestId }) => {
      const header = document.querySelector<HTMLElement>(
        `[data-testid="${headerTestId}"]`,
      );
      const row = document.querySelector<HTMLElement>(
        `[data-testid="${rowTestId}"]`,
      );
      if (!header) throw new Error(`no header: ${headerTestId}`);
      if (!row) throw new Error(`no row: ${rowTestId}`);

      const describe = (el: Element) => {
        const box = el.getBoundingClientRect();
        return {
          testid: (el as HTMLElement).dataset?.testid ?? "(no testid)",
          left: Math.round(box.left),
          width: Math.round(box.width),
          text: (el.textContent ?? "").trim().slice(0, 24),
        };
      };

      const flatten = (parent: HTMLElement) =>
        Array.from(parent.children).flatMap((child) =>
          (child as HTMLElement).dataset?.testid === openButtonTestId
            ? Array.from(child.children).map(describe)
            : [describe(child)],
        );

      return {
        header: flatten(header),
        row: flatten(row),
        headerClientWidth: header.clientWidth,
        headerScrollWidth: header.scrollWidth,
        rowClientWidth: row.clientWidth,
        rowScrollWidth: row.scrollWidth,
      };
    },
    { headerTestId, rowTestId, openButtonTestId },
  );
}

/**
 * The assertion itself, shared by both tables.
 *
 * Failures name the column and both offsets, because "expected 1383 to be
 * 1710" on its own sends the reader to the DOM to work out which column that
 * even was.
 */
function expectColumnsAligned(geometry: TableGeometry, table: string): void {
  const { header, row } = geometry;

  expect(
    row.length,
    `${table}: the header renders ${header.length} cells and the row ${row.length}. ` +
      `A column exists on one and not the other, so nothing below can line up.\n` +
      `  header: ${header.map((c) => c.testid).join(", ")}\n` +
      `  row:    ${row.map((c) => c.testid).join(", ")}`,
  ).toBe(header.length);

  // No overflow: a row wider than its own box is the mechanism by which the
  // two lists drift apart, and it is worth naming separately from the
  // per-column comparison so the cause is in the failure and not just the
  // symptom.
  expect(
    geometry.rowScrollWidth,
    `${table}: the row holds ${geometry.rowScrollWidth} px of cells in a ` +
      `${geometry.rowClientWidth} px track. Raise the track's min-width to ` +
      `the sum of the declared column widths.`,
  ).toBeLessThanOrEqual(geometry.rowClientWidth + 1);
  expect(
    geometry.headerScrollWidth,
    `${table}: the header holds ${geometry.headerScrollWidth} px of cells in ` +
      `a ${geometry.headerClientWidth} px track, so its cells are being ` +
      `squeezed below their declared widths.`,
  ).toBeLessThanOrEqual(geometry.headerClientWidth + 1);

  for (const [index, headerCell] of header.entries()) {
    const rowCell = row[index];
    expect(
      rowCell.left,
      `${table} column ${index} (header "${headerCell.text}" / row ` +
        `"${rowCell.text}"): the heading starts at ${headerCell.left} px and ` +
        `its data at ${rowCell.left} px — ${Math.abs(rowCell.left - headerCell.left)} px apart.`,
    ).toBeCloseTo(headerCell.left, 0);
    expect(
      rowCell.width,
      `${table} column ${index} (header "${headerCell.text}"): the heading is ` +
        `${headerCell.width} px wide and its data ${rowCell.width} px. Both ` +
        `sides declare the width, so one of them has drifted.`,
    ).toBeCloseTo(headerCell.width, 0);
  }
}

let sharedPage: Page;
let seedFailed = false;
let seed: SeedSummary;

test.describe.serial("@tables column headers align with their data", () => {
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

  test("A1) the Components table's headings sit above their own cells", async () => {
    const portal = new PortalPage(sharedPage);
    await portal.selectTab("components");
    await portal.expectComponentsTabReady();
    // `expectComponentsTabReady()` only waits for the `components-virtual`
    // container to mount — Virtuoso mounts that container as soon as the
    // page's data has landed, but it paints on an *estimated* row height
    // first and only commits real row nodes after its own measurement pass
    // (the same hazard `expectVulnerabilityWindowSettled()` below exists
    // for). On a loaded CI runner that measurement pass can still be
    // in-flight the instant this test's own `page.evaluate` looks for the
    // first `component-row` node, throwing "no row: component-row" even
    // though the row lands moments later — reproduced against the nightly
    // run's own trace (issue #260): the post-failure DOM snapshot shows a
    // fully rendered table. Waiting for the row to attach — not a fixed
    // sleep, Playwright's own auto-retrying wait — closes that window.
    await sharedPage
      .getByTestId("component-row")
      .first()
      .waitFor({ state: "attached", timeout: 10_000 });
    expectColumnsAligned(
      await readTableGeometry(
        sharedPage,
        "components-header",
        "component-row",
        "component-row-open",
      ),
      "components",
    );
  });

  test("A2) the Vulnerabilities table's headings sit above their own cells", async () => {
    const portal = new PortalPage(sharedPage);
    await portal.selectTab("vulnerabilities");
    // Not `expectVulnerabilitiesTabReady()` — that only waits for the
    // `vulnerabilities-virtual` container, which Virtuoso mounts before its
    // estimated-height measurement pass finishes committing real rows (see
    // the A1 comment above; same hazard, and the fix already lives in the
    // harness for this tab). `expectVulnerabilityWindowSettled()` waits for
    // that pass to land before this test reads row geometry.
    await portal.expectVulnerabilityWindowSettled();
    expectColumnsAligned(
      await readTableGeometry(
        sharedPage,
        "vulnerabilities-header",
        "vulnerability-row",
        "vulnerability-row-open",
      ),
      "vulnerabilities",
    );
  });
});
