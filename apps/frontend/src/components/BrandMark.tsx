// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * BrandMark — the TRUSCA symbol ("Hex Check": package hexagon + verification
 * check), picked in the W1 rebrand.
 *
 * Canonical in-app rendering of the mark (the same geometry ships as
 * public/favicon.svg and the docs-site logo). Reuse this component anywhere
 * the symbol is needed instead of re-inlining the paths.
 *
 * Palette is fixed brand colour (not theme tokens) so the tile reads
 * identically on any surface: a dark-slate tile (#0f172a) with the hexagon
 * in paper (#fafafa) and the check in teal (#2dd4bf) as the brand accent.
 * Clean dark base + a teal pop — not a flat black, not a loud gradient.
 */

export function BrandMark({
  size = 24,
  onInk = false,
}: {
  size?: number;
  /**
   * Drop the dark tile.
   *
   * The mark is a slate tile carrying a paper hexagon and a teal check —
   * built to sit on light surfaces. On the ink global bar the tile is
   * near-invisible against the bar itself, so the mark reads as a floating
   * fragment. Without the tile the hexagon and check stand on the bar
   * directly, which is what the tile was drawing for them anyway.
   */
  onInk?: boolean;
}) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      width={size}
      height={size}
      role="img"
      aria-hidden
      focusable="false"
    >
      {onInk ? null : (
        <rect width="32" height="32" rx="8" fill="#0f172a" />
      )}
      <path
        d="M16 6.5 L24.2 11.25 V20.75 L16 25.5 L7.8 20.75 V11.25 Z"
        fill="none"
        stroke="#fafafa"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path
        d="M12.6 16.2 L15.1 18.7 L19.6 13.4"
        fill="none"
        stroke="#2dd4bf"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
