// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Keep a screen's filters in the address bar.
 *
 * A filter held in component state is invisible: reloading loses it, the
 * Back button leaves the page instead of undoing the last narrowing, and a
 * URL sent to a colleague shows them something else. Five screens still work
 * that way, and the three that were fixed before this each grew their own
 * copy of the same twenty lines, with small differences nobody chose.
 *
 * The rules those copies converged on, now in one place:
 *
 *   - A parser that rejects anything it does not recognise, so a hand-edited
 *     or stale URL falls back to the default rather than being forwarded to
 *     the backend. Half these values end up in a query string.
 *   - The default is absent from the URL. `?status=open` when open is what
 *     you get anyway is noise, and it makes a shared link look deliberate
 *     when it is not.
 *   - Changing a filter clears the page. Otherwise narrowing a result set
 *     while on page 4 lands on a page that no longer exists, which reads as
 *     an empty screen.
 *   - `replace: false`, so each change is a history entry and Back undoes
 *     the last one. The vulnerability and obligation drawers already work
 *     this way; note that ProjectDetailPage deliberately does the opposite
 *     for its own drawer toggles, so this is a rule for list filters rather
 *     than a convention the whole app follows.
 *
 * The value is read from the URL on every render rather than mirrored into
 * state. That is the half `ScansPage` was missing: it seeded a `useState`
 * from the URL once and wrote back on change, so the address bar and the
 * screen agreed until the user pressed Back, and then quietly did not.
 */
import { useCallback, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";

/** The page parameter every list screen shares. */
export const PAGE_PARAM = "page";

/**
 * Upper bound on a page number, matching `PAGE_MAX` in
 * `apps/backend/core/pagination.py`. Anything above it is a 422 from the
 * backend and a red banner on the screen, which is the failure this hook
 * exists to keep a hand-edited URL from causing.
 */
export const PAGE_MAX = 1_000_000;

export interface UrlParamOptions<T> {
  /**
   * Turn the raw parameter into a value. Called with null when the parameter
   * is absent. Must return the default rather than throwing for anything it
   * does not accept.
   */
  parse: (raw: string | null) => T;
  /**
   * Turn a value back into a parameter, or null to remove it. Returning null
   * for the default is what keeps it out of the URL.
   */
  serialize: (value: T) => string | null;
  /**
   * Whether changing this value should send the reader back to page 1.
   * True for anything that narrows a result set; false for the page itself
   * and for things that do not change what is in the list.
   */
  resetsPage?: boolean;
}

/** Options a single write can override. */
export interface SetUrlValueOptions {
  /**
   * Write over the current history entry instead of adding one. For
   * corrections the reader did not ask for, e.g. snapping a page number
   * down to a range the data actually has.
   */
  replace?: boolean;
}

export type UrlValueSetter<T> = (
  next: T | ((prev: T) => T),
  opts?: SetUrlValueOptions,
) => void;

/**
 * Read and write one URL parameter.
 *
 * Returns the current value and a setter, in the shape of `useState`, so a
 * screen migrating from local state changes its declaration and nothing else.
 */
export function useUrlParam<T>(
  key: string,
  { parse, serialize, resetsPage = true }: UrlParamOptions<T>,
): [T, UrlValueSetter<T>] {
  const [searchParams, setSearchParams] = useSearchParams();
  const value = parse(searchParams.get(key));

  // `serialize` is typically an inline arrow, so a fresh identity every
  // render. Holding it in a ref keeps the setter's identity stable without
  // an exhaustive-deps suppression, and without the stale closure a
  // suppression would leave behind.
  const serializeRef = useRef(serialize);
  serializeRef.current = serialize;

  // Needed for the same reason as `serializeRef`: the functional setter
  // form below has to parse the pending parameters, and `parse` is an
  // inline arrow at most call sites.
  const parseRef = useRef(parse);
  parseRef.current = parse;

  // What the URL will hold once the writes issued so far have committed.
  // `setSearchParams` hands its updater the parameters that were current
  // when the setter was built, not live ones, so two writes in the same
  // React batch would otherwise both start from the pre-batch URL and the
  // second would undo the first. This only composes writes from THIS hook
  // instance; two different parameters written in one batch still race,
  // which is why changing a filter clears the page inside the hook rather
  // than by the screen calling `setPage(1)` beside it.
  const pendingRef = useRef(searchParams);
  useEffect(() => {
    pendingRef.current = searchParams;
  }, [searchParams]);

  const setValue = useCallback(
    (next: T | ((prev: T) => T), opts?: SetUrlValueOptions) => {
      const base = pendingRef.current;
      // The functional form reads from the pending parameters rather than
      // from a value the caller closed over. Two Next clicks fast enough to
      // land in one batch both see page 1 otherwise, and one of them is
      // lost.
      const resolved =
        typeof next === "function"
          ? (next as (prev: T) => T)(parseRef.current(base.get(key)))
          : next;
      const raw = serializeRef.current(resolved);
      const clearsPage = resetsPage && base.has(PAGE_PARAM);

      // Setting a value to what it already is must not add a history entry.
      // A debounced search writes on a timer, and a Back that restores an
      // earlier term re-arms that timer; without this the same value would
      // be pushed again and Back would appear to do nothing.
      //
      // The check has to happen here rather than inside the updater below:
      // returning the previous parameters from an updater still navigates,
      // so the entry would be pushed with the query string unchanged.
      if ((raw ?? null) === (base.get(key) ?? null) && !clearsPage) return;

      const out = new URLSearchParams(base);
      if (raw === null) out.delete(key);
      else out.set(key, raw);
      if (resetsPage) out.delete(PAGE_PARAM);
      pendingRef.current = out;

      setSearchParams(out, { replace: opts?.replace ?? false });
    },
    [key, resetsPage, setSearchParams],
  );

  return [value, setValue];
}

/**
 * The page number, which every list screen spells the same way.
 *
 * Kept as its own hook rather than a `useUrlParam` call at five sites: the
 * clamping is the same everywhere, and a page outside the backend's range
 * is a 422 rather than a gently ignored filter, at either end, now that the
 * page is something a link or a bookmark can carry.
 */
export function usePageParam(): [number, UrlValueSetter<number>] {
  return useUrlParam<number>(PAGE_PARAM, {
    parse: (raw) => {
      if (!raw) return 1;
      const n = Number.parseInt(raw, 10);
      if (!Number.isFinite(n) || n < 1) return 1;
      return Math.min(n, PAGE_MAX);
    },
    serialize: (page) => (page <= 1 ? null : String(Math.min(page, PAGE_MAX))),
    resetsPage: false,
  });
}

/**
 * Bring a page number back inside the range the data actually has.
 *
 * A link or a bookmark can name page 5 of a list that now has one page. The
 * request comes back empty, and without this the footer reads "Page 5 of 1"
 * beside an empty table. Waits for a settled response so it does not fire
 * against the zero totals of a load in progress, and replaces the history
 * entry because the reader did not ask for this move.
 */
export function useClampPage(
  page: number,
  totalPages: number,
  setPage: UrlValueSetter<number>,
  settled: boolean,
): void {
  useEffect(() => {
    if (!settled) return;
    if (page > totalPages) setPage(Math.max(1, totalPages), { replace: true });
  }, [settled, page, totalPages, setPage]);
}

/**
 * A value from a fixed set, e.g. a tab or a status filter.
 *
 * `allowed` is the whole vocabulary; anything else is treated as absent.
 */
export function useUrlEnum<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): [T, UrlValueSetter<T>] {
  return useUrlParam<T>(key, {
    parse: (raw) =>
      raw && (allowed as readonly string[]).includes(raw) ? (raw as T) : fallback,
    serialize: (value) => (value === fallback ? null : value),
  });
}

/** A free-text filter, trimmed, with an upper bound on both sides so a URL cannot be a payload. */
export function useUrlText(
  key: string,
  maxLength = 200,
): [string, UrlValueSetter<string>] {
  return useUrlParam<string>(key, {
    parse: (raw) => (raw ?? "").trim().slice(0, maxLength),
    // The same bound on the way out. Enforcing it only on the way in leaves
    // the address bar showing a filter the list was never narrowed by.
    serialize: (value) => value.trim().slice(0, maxLength) || null,
  });
}

/** A boolean filter, present in the URL only when it is on. */
export function useUrlFlag(key: string): [boolean, UrlValueSetter<boolean>] {
  return useUrlParam<boolean>(key, {
    parse: (raw) => raw === "1",
    serialize: (value) => (value ? "1" : null),
  });
}
