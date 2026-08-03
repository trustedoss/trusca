// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Per-tab search URL parameters — S1-5.
 *
 * The Components / Vulnerabilities / Licenses / Obligations tabs each own a
 * search box, and all four used to write the SAME `?search=` key. Switching
 * tabs therefore carried the previous tab's term into the next one's box and
 * filtered by it — "lodash" typed on Components silently narrowed the CVE
 * list a click later. Compliance had already solved this by prefixing its
 * params (`compliance_search`); this module extends that convention to the
 * remaining four so each tab's term is its own.
 *
 * Backward compatibility: a deep link minted before this change carries the
 * shared `?search=`. Only ONE tab is mounted at a time (Radix `TabsContent`
 * unmounts inactive tabs), so the active tab reads the legacy key as a
 * fallback on hydration and the first URL write replaces it with the prefixed
 * key. Old links keep working; the URL upgrades itself in place.
 */

export const COMPONENTS_SEARCH_PARAM = "components_search";
export const VULNERABILITIES_SEARCH_PARAM = "vulnerabilities_search";
export const LICENSES_SEARCH_PARAM = "licenses_search";
export const OBLIGATIONS_SEARCH_PARAM = "obligations_search";

/** The pre-S1-5 shared key, still honoured when hydrating. */
export const LEGACY_SEARCH_PARAM = "search";

/**
 * Read a tab's search term, falling back to the legacy shared key.
 *
 * Used in `useState` initialisers, so it must stay pure and cheap.
 */
export function readTabSearchParam(
  params: URLSearchParams,
  key: string,
): string {
  return params.get(key) ?? params.get(LEGACY_SEARCH_PARAM) ?? "";
}

/**
 * Write a tab's search term, retiring the legacy shared key.
 *
 * Mutates `next` in place to match how the tabs' URL-sync effects build their
 * `URLSearchParams`. Dropping `LEGACY_SEARCH_PARAM` on every write is what
 * makes the migration one-way: once any tab has synced, the ambiguous key is
 * gone from the URL and cannot leak into a sibling tab.
 */
export function writeTabSearchParam(
  next: URLSearchParams,
  key: string,
  value: string,
): void {
  if (value) next.set(key, value);
  else next.delete(key);
  next.delete(LEGACY_SEARCH_PARAM);
}
