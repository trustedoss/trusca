// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Declared width in px of each optional column of the Components table, the
 * Tailwind width class on its header and row cell (w-24 = 96, and so on).
 * `version` is required and always shows. The track's minimum width follows
 * the visible set, as on the Vulnerabilities table; keep the two in step and
 * `tests/e2e/tableColumnAlignment.spec.ts` catches a drift in a browser.
 */
export const COMPONENT_COLUMN_WIDTH_PX: Readonly<Record<string, number>> = {
  type: 96,
  version: 112,
  license: 128,
  policy: 96,
  usage: 96,
  malicious: 80,
  eol: 64,
  currency: 80,
  severity: 96,
  vulns: 48,
};

/** px-6 on both sides. */
const TRACK_PADDING_PX = 48;
/** The Name column's floor (min-w-[120px]). */
const NAME_FLOOR_PX = 120;
/** gap-3 between cells. */
const COLUMN_GAP_PX = 12;

export function componentTrackMinWidth(
  visibleColumns: ReadonlySet<string>,
): number {
  const ids = Object.keys(COMPONENT_COLUMN_WIDTH_PX).filter(
    (id) => id === "version" || visibleColumns.has(id),
  );
  const cells = ids.reduce((s, id) => s + COMPONENT_COLUMN_WIDTH_PX[id], 0);
  // Name is a cell too, so the gutters number one per cell after the first.
  return TRACK_PADDING_PX + NAME_FLOOR_PX + cells + COLUMN_GAP_PX * ids.length;
}
