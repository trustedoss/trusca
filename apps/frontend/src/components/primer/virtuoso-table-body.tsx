// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { type ComponentPropsWithoutRef, forwardRef } from "react";

/**
 * The table body for a Virtuoso-backed grid, placed on the one element between
 * the table and its rows that the accessibility tree will not flatten.
 *
 * Everything Virtuoso renders between the two is a plain `div`, wrapper,
 * scroller, viewport, list, per-item box, and a plain `div` disappears from
 * the accessibility tree, so the rows read as owned by the table. All except
 * the scroller: it carries `tabindex` so the list can be scrolled from the
 * keyboard, and a focusable element is never flattened. It has to hold a role
 * the table allows, so it holds `rowgroup`.
 *
 * Two things were tried first and are worth not re-trying.
 * `role="presentation"` on the wrappers: axe rejects it, and correctly. A
 * presentational element is still an owned child, and `table` owns rows and
 * rowgroups, not presentation. `rowgroup` on the wrapper div outside Virtuoso:
 * the focusable scroller then sits INSIDE the rowgroup and is reported there
 * instead.
 *
 * Module scope, not inline: a `components` object rebuilt each render makes
 * Virtuoso remount its scroller, losing scroll position and re-measuring.
 * `role` goes after the spread so Virtuoso's own props cannot overwrite it.
 *
 * Shared rather than copied per tab: it was written twice already, and the
 * third grid needing it is what made the duplication worth removing.
 */
export const VIRTUOSO_TABLE_BODY = {
  Scroller: forwardRef<HTMLDivElement, ComponentPropsWithoutRef<"div">>(
    function VirtuosoScroller(props, ref) {
      return <div ref={ref} {...props} role="rowgroup" />;
    },
  ),
};
