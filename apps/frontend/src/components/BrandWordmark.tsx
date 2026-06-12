/**
 * BrandWordmark — the "TRUSCA" lettering with the SCA half in the brand
 * teal accent (#0f766e), the rest in ink (inherits the surrounding text
 * colour). Pairs with BrandMark; see docs/brand-trusca.md.
 *
 * The split is intentional and brand-fixed (TRU + SCA), so the strings are
 * not translated — "TRUSCA" is the product name, identical in every locale.
 * The two spans sit flush so the accessible text content reads "TRUSCA".
 * Colour is a fixed hex (not a theme token) to match the mark, which renders
 * identically on any surface.
 */

export function BrandWordmark() {
  return (
    <span>
      TRU<span style={{ color: "#0f766e" }}>SCA</span>
    </span>
  );
}
