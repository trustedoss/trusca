/**
 * BrandWordmark — the "TRUSCA" lettering in ink (inherits the surrounding
 * text colour / foreground). Weight and size come from context (the sidebar
 * header is semibold-sm; the auth lockup sizes it up). The brand colour lives
 * in the mark (BrandMark) and the teal check; the wordmark stays neutral ink
 * so the lockup reads clean. See docs/brand-trusca.md and BrandLockup.
 *
 * "TRUSCA" is the product name, identical in every locale, so it is not
 * translated.
 */

export function BrandWordmark() {
  return <span>TRUSCA</span>;
}
