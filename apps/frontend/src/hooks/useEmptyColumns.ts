// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useCallback, useRef, useState } from "react";

/**
 * "Hide a column when no loaded row has a value for it."
 *
 * A column of dashes in every row costs width and says nothing. The rule is
 * decided per loaded rows and is sticky: once any row has shown a value for a
 * column during the session, the column stays, so a virtualized table that
 * loads its next page does not make a column pop in and out. Nothing is
 * decided while there are no rows (loading, or an empty result), and the memory
 * resets when `resetKey` changes (another project, another scan).
 *
 * A column the user switched on in the Columns menu is "pinned" and never
 * auto-hidden. A stored selection cannot say whether a column was chosen or
 * just saved along with the others, so only a toggle made through the menu
 * counts, and it is stored per table.
 */
export interface EmptyColumnRule<T> {
  /** Column id, the same id the Columns menu uses. */
  id: string;
  /** True when the row has something to show in this column. */
  hasValue: (row: T) => boolean;
}

/**
 * Pure core: records the ids that have data in `seen` and returns the ids that
 * are still empty. Exported for tests.
 */
export function collectEmptyColumns<T>(
  rows: readonly T[],
  rules: readonly EmptyColumnRule<T>[],
  seen: Set<string>,
): Set<string> {
  const empty = new Set<string>();
  if (rows.length === 0) return empty;
  for (const rule of rules) {
    if (!seen.has(rule.id) && rows.some(rule.hasValue)) seen.add(rule.id);
    if (!seen.has(rule.id)) empty.add(rule.id);
  }
  return empty;
}

/** The set a table should render: the chosen columns minus the empty, unpinned ones. */
export function effectiveColumns(
  chosen: ReadonlySet<string>,
  empty: ReadonlySet<string>,
  pinned: ReadonlySet<string>,
): Set<string> {
  const out = new Set<string>();
  for (const id of chosen) {
    if (empty.has(id) && !pinned.has(id)) continue;
    out.add(id);
  }
  return out;
}

/** Ids empty across every loaded row, remembered for the session per `resetKey`. */
export function useEmptyColumns<T>(
  rows: readonly T[],
  rules: readonly EmptyColumnRule<T>[],
  resetKey: string = "",
): Set<string> {
  const memory = useRef<{ key: string; seen: Set<string> }>({
    key: resetKey,
    seen: new Set(),
  });
  if (memory.current.key !== resetKey) {
    memory.current = { key: resetKey, seen: new Set() };
  }
  return collectEmptyColumns(rows, rules, memory.current.seen);
}

function readPinned(storageKey: string): Set<string> {
  try {
    const raw = window.localStorage.getItem(storageKey);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return new Set(
      Array.isArray(parsed)
        ? parsed.filter((v): v is string => typeof v === "string")
        : [],
    );
  } catch {
    return new Set();
  }
}

/**
 * Columns the user turned on in the Columns menu. `notePicked(prev, next)`
 * takes the visibility before and after a menu change and pins what was added.
 */
export function usePinnedColumns(storageKey: string) {
  const [pinned, setPinned] = useState<Set<string>>(() =>
    readPinned(storageKey),
  );
  const notePicked = useCallback(
    (prev: ReadonlySet<string>, next: ReadonlySet<string>) => {
      const added = [...next].filter((id) => !prev.has(id));
      if (added.length === 0) return;
      setPinned((current) => {
        const merged = new Set([...current, ...added]);
        try {
          window.localStorage.setItem(storageKey, JSON.stringify([...merged]));
        } catch {
          /* private mode / quota: the pin lasts for this session only. */
        }
        return merged;
      });
    },
    [storageKey],
  );
  return { pinned, notePicked };
}
