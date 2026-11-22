// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Column layout of the Vulnerabilities findings table: which columns a fresh
 * user sees, how wide each one is declared, and the track width that follows.
 */

/**
 * Columns a fresh user (no stored visibility) does not see. With every column
 * on, the table needs 1636 px, and at 1440 px with the sidebar open the pane
 * is 1184 px, so SLA due was cut at the right edge and Owner, Status and
 * Discovered sat off screen. Title repeats the drawer's summary, Owner shows
 * only "Assigned" or "Unassigned", and Discovered is the start of the SLA
 * clock the SLA column already reports. All three stay one click away in the
 * Columns menu, and a stored selection is always respected.
 */
export const VULN_DEFAULT_HIDDEN_COLUMNS: readonly string[] = [
  "title",
  "assignee",
  "discovered",
];

/**
 * Declared width of each column in px, in table order. Each number is the
 * Tailwind width class on that column's header and row cells (w-36 = 144, and
 * so on); `component` is the flexible column's floor (min-w-[200px]) and
 * `select` is the checkbox column. Keep the two in step: the track's minimum
 * width is computed from this table, a unit test reads the classes back
 * against it, and `tests/e2e/tableColumnAlignment.spec.ts` fails in a browser
 * when a header and its data disagree.
 */
export const VULN_COLUMN_WIDTH_PX: Readonly<Record<string, number>> = {
  select: 16,
  cve_id: 144,
  title: 224,
  component: 200,
  severity: 96,
  cvss: 56,
  epss: 64,
  reachable: 112,
  sla_due: 176,
  assignee: 128,
  status: 128,
  discovered: 112,
};

/** px-6 on both sides of the header and each row. */
const TRACK_PADDING_PX = 48;
/** gap-3 between cells, the same in the header and the rows. */
const COLUMN_GAP_PX = 12;

/**
 * Minimum width of the table track for the visible columns: the sum of their
 * declared widths, the gutters between them and the side padding. Below this
 * the track scrolls sideways instead of squeezing cells, which is what kept
 * headers and data apart before.
 */
export function vulnTrackMinWidth(visibleColumns: ReadonlySet<string>): number {
  const ids = [
    "select",
    ...Object.keys(VULN_COLUMN_WIDTH_PX).filter(
      (id) => id !== "select" && visibleColumns.has(id),
    ),
  ];
  const cells = ids.reduce((sum, id) => sum + VULN_COLUMN_WIDTH_PX[id], 0);
  return TRACK_PADDING_PX + cells + COLUMN_GAP_PX * (ids.length - 1);
}
