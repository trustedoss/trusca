/**
 * searchParamsToggle — chart segment + facet toggle helper (W9-#57).
 *
 * Distribution charts (severity / license) deep-link clicks into a filter
 * by writing a query-string facet (`?severity=critical`, `?license_category=
 * forbidden`, ...). Before W9-#57 the click handler always *set* the value,
 * so a second click on the same segment was a no-op and users had to hunt
 * down a chip-clear control to remove the filter.
 *
 * W9-#57 changes the semantics: clicking a segment whose value is the **only
 * active value** for that facet *removes* the facet entirely (toggles off).
 * Clicking a different value or a value that isn't currently active still
 * sets the new value. Multi-value facets that include the clicked value as
 * one of several entries also fall back to "set to this single value" — the
 * chart click is always a single-segment focus, so we collapse the multi-
 * selection to that one bucket rather than trying to splice it.
 *
 * The function returns a fresh `URLSearchParams` instance so callers can
 * safely pipe it into React Router's `setSearchParams((prev) => ...)` form
 * without leaking mutations into the previous value.
 */

export interface ToggleSearchParamOptions {
  /**
   * Extra query-string mutations to apply in addition to the toggle. Used
   * for chart-driven deep-links that also need to change the active tab,
   * sub-view, etc. (e.g. `{ tab: "vulnerabilities" }`). Falsy / undefined
   * values delete the key.
   */
  also?: Record<string, string | null | undefined>;
}

export function toggleSearchParam(
  params: URLSearchParams,
  key: string,
  value: string,
  options: ToggleSearchParamOptions = {},
): URLSearchParams {
  const next = new URLSearchParams(params);
  const current = next.getAll(key);
  const isOnlyActive = current.length === 1 && current[0] === value;
  next.delete(key);
  if (!isOnlyActive) {
    next.append(key, value);
  }
  if (options.also) {
    for (const [k, v] of Object.entries(options.also)) {
      if (v == null || v === "") {
        next.delete(k);
      } else {
        next.set(k, v);
      }
    }
  }
  return next;
}

/**
 * In-array variant for components that hold the active filter in React
 * state as an array (`useState<Severity[]>`). Same semantics as
 * `toggleSearchParam`: clicking the only-active value clears the array;
 * any other click replaces with `[value]`.
 *
 * Kept as a tiny named helper rather than inlined so the toggle rule lives
 * in one place — every chart consumer ends up with identical behaviour.
 */
export function toggleSingleValue<T>(current: readonly T[], value: T): T[] {
  const isOnlyActive = current.length === 1 && current[0] === value;
  return isOnlyActive ? [] : [value];
}

/**
 * Nullable single-value variant for components that hold the active filter
 * as `T | null` (e.g. ProjectListPage's severity/license filter). Returns
 * `null` when the clicked value matches the current selection, the new
 * value otherwise.
 */
export function toggleNullable<T>(current: T | null, value: T): T | null {
  return current === value ? null : value;
}
