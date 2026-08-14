// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Keep the browser tab named after the screen.
 *
 * `index.html` sets "TRUSCA" once and nothing changed it afterwards, so every
 * tab, every history entry, and every bookmark carried the same four letters.
 * Two tabs open on two projects were indistinguishable; browser history was
 * unusable for getting back to a CVE you looked at this morning.
 *
 * Most screens get this through `PageHeader`, which already receives the
 * translated page title. The hook is for the surfaces that render their own
 * heading outside the header component, and for titles that carry a record's
 * name ("django 4.2 · Components · TRUSCA").
 */
import { useEffect } from "react";

/** The product name that ends every title, matching `index.html`. */
export const TITLE_BRAND = "TRUSCA";

/** The separator between title segments. */
const SEPARATOR = " · ";

/**
 * Compose a document title from its segments, most specific first, ending in
 * the brand. Empty and nullish segments are dropped so a caller can pass a
 * record name that has not loaded yet without producing "· · TRUSCA".
 */
export function documentTitle(...segments: (string | null | undefined)[]): string {
  const parts = segments.filter((s): s is string => Boolean(s && s.trim()));
  return [...parts, TITLE_BRAND].join(SEPARATOR);
}

/**
 * Set `document.title` for as long as the component is mounted.
 *
 * Segments are joined most-specific-first. With nothing to say — every segment
 * still loading, or a caller opting out — the tab is left as it is rather than
 * set to the bare brand: a screen whose name arrives a tick late would
 * otherwise flash "TRUSCA" first, which is the state this hook exists to end.
 *
 * The title is not restored on unmount. In a single-page app the next screen
 * sets its own on the same tick, and restoring first would make the tab
 * flicker back to the previous name.
 */
export function useDocumentTitle(
  ...segments: (string | null | undefined)[]
): void {
  const hasName = segments.some((s) => Boolean(s && s.trim()));
  const title = documentTitle(...segments);
  useEffect(() => {
    if (!hasName) return;
    document.title = title;
  }, [hasName, title]);
}
