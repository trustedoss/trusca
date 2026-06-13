/**
 * BrandWordmark — the "TRUSCA" lettering in a single brand-teal colour
 * (#0f766e), matching the mark tile. Weight / size inherit from the
 * surrounding context (e.g. the sidebar header is semibold-sm; the auth
 * lockup sizes it up). See docs/brand-trusca.md and BrandLockup.
 *
 * "TRUSCA" is the product name, identical in every locale, so it is not
 * translated. Colour is a fixed hex (not a theme token) to match the mark,
 * which renders identically on any surface.
 */

export function BrandWordmark() {
  return <span style={{ color: "#0f766e" }}>TRUSCA</span>;
}
