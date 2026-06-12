/**
 * BrandMark — the TRUSCA symbol ("Hex Check": package hexagon + verification
 * check), picked in the W1 rebrand (see docs/brand-trusca.md).
 *
 * Canonical in-app rendering of the mark (the same geometry ships as
 * public/favicon.svg and the docs-site logo). Used by the AppShell collapsed
 * rail; reuse this component anywhere the symbol is needed instead of
 * re-inlining the paths.
 *
 * Palette is fixed brand ink/paper (not theme tokens) so the tile reads
 * identically on any surface, matching the favicon.
 */

export function BrandMark({ size = 24 }: { size?: number }) {
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
      <rect width="32" height="32" rx="7" fill="#18181b" />
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
