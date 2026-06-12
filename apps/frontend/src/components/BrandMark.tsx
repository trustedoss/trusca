/**
 * BrandMark — the TRUSCA symbol ("Hex Check": package hexagon + verification
 * check), picked in the W1 rebrand (see docs/brand-trusca.md).
 *
 * Canonical in-app rendering of the mark (the same geometry ships as
 * public/favicon.svg and the docs-site logo). Reuse this component anywhere
 * the symbol is needed instead of re-inlining the paths.
 *
 * Palette is fixed brand colour (not theme tokens) so the tile reads
 * identically on any surface: a teal gradient tile (#2dd4bf → #0f766e) with
 * the hexagon + check in paper (#fafafa). Teal is the TRUSCA brand colour;
 * the wordmark (BrandWordmark) uses the same teal. The gradient id is
 * per-instance (useId) so multiple marks on one page never collide.
 */
import { useId } from "react";

export function BrandMark({ size = 24 }: { size?: number }) {
  const gradId = useId();
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
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#2dd4bf" />
          <stop offset="1" stopColor="#0f766e" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#${gradId})`} />
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
        stroke="#fafafa"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
