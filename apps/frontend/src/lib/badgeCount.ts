// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * How a count is written on a badge.
 *
 * Lived in `HeaderBell.tsx` until the sidebar grew badges of its own (C1).
 * One rule, in one place: two badges a few pixels apart that disagreed about
 * where to cap would read as a bug in the numbers rather than in the pixels.
 */

/** Above this the exact number stops being useful and starts breaking layout. */
const BADGE_CAP = 99;

/** Returns "" when there is nothing to show, so callers can test one value. */
export function formatBadge(count: number): string {
  if (count <= 0) return "";
  if (count > BADGE_CAP) return `${BADGE_CAP}+`;
  return String(count);
}
