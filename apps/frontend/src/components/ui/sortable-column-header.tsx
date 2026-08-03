// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

/**
 * SortableColumnHeader — W4-B-prep shared primitive.
 *
 * Replaces the previous "Sort by ▼ + Order ▼" toolbar pair on Components,
 * Vulnerabilities, and any future list. Click the header to cycle the sort
 * state for that column:
 *
 *   unset → ascending → descending → unset
 *
 * The component is purely presentational. URL/state plumbing lives in the
 * caller (parent tab), which:
 *   1. Reads the current sort (e.g. from `useSearchParams`).
 *   2. Passes it down via `currentSort`.
 *   3. Receives the next state via `onSort` and writes it back.
 *
 * Accessibility:
 *   - Rendered as a `<button>` for full keyboard support.
 *   - The accessible name carries both the current sort state and what the
 *     next click does ("Severity — sorted ascending, click to sort
 *     descending"), in EN and KO.
 *   - Icon swaps so the cue isn't color-only.
 *
 * Where `aria-sort` lives
 * -----------------------
 * On the `columnheader` that CONTAINS this button, never on the button.
 * `aria-sort` is allowed only on `columnheader` / `rowheader`; it used to sit
 * here, where it was invalid ARIA that assistive technology ignored — the
 * attribute announced nothing while looking like it did, and four unit tests
 * asserted it, which is how it survived (G0-5).
 *
 * For a while there was nowhere to move it to, because the callers were flex
 * `div`s with no table semantics at all. They have them now, so the caller
 * puts `role="columnheader"` on the cell and `ariaSortFor()` on the same
 * element. Deriving the value from one exported helper is what keeps the two
 * halves — what the header announces and what the button announces — from
 * drifting apart.
 *
 * The component does not manage focus or table semantics; the caller is
 * responsible for wrapping headers in the appropriate element.
 */

export type SortOrder = "asc" | "desc";

export interface SortState {
  /** Column id currently sorted. */
  key: string;
  order: SortOrder;
}

export interface SortableColumnHeaderProps {
  /** Column identifier used to compare against `currentSort.key`. */
  column: string;
  /** Visible label (already localized). */
  label: string;
  /**
   * Globally active sort, or `null` for unsorted. Only this column is
   * highlighted when `currentSort.key === column`.
   */
  currentSort: SortState | null;
  /**
   * Called with the next sort state. `null` when the cycle returns to
   * unsorted. Callers translate this into URL params / API params.
   */
  onSort: (next: SortState | null) => void;
  /** Optional testId; defaults to `column-header-${column}` when omitted. */
  testId?: string;
  className?: string;
}

/**
 * The `aria-sort` value for the columnheader that hosts this column's button.
 *
 * Belongs on the cell, not on the button — see the note at the top. Exported
 * so the header cell and the button read the same state from one place: the
 * button's accessible name says "sorted ascending" and the cell says
 * `aria-sort="ascending"`, and there is no second derivation to get wrong.
 *
 * A column that is not the active sort is `"none"`, not omitted: on a sortable
 * column the absence of the attribute reads as "not sortable".
 */
export function ariaSortFor(
  column: string,
  currentSort: SortState | null,
): "none" | "ascending" | "descending" {
  if (currentSort?.key !== column) return "none";
  return currentSort.order === "asc" ? "ascending" : "descending";
}

/** Next state in the cycle: unset → asc → desc → unset. */
export function nextSortState(
  column: string,
  current: SortState | null,
): SortState | null {
  if (!current || current.key !== column) {
    return { key: column, order: "asc" };
  }
  if (current.order === "asc") {
    return { key: column, order: "desc" };
  }
  return null;
}

export function SortableColumnHeader({
  column,
  label,
  currentSort,
  onSort,
  testId,
  className,
}: SortableColumnHeaderProps) {
  const { t } = useTranslation("common");
  const isActive = currentSort?.key === column;
  const order = isActive ? currentSort?.order : null;
  const stateAriaKey =
    order === "asc"
      ? "sort.aria_ascending"
      : order === "desc"
        ? "sort.aria_descending"
        : "sort.aria_unsorted";

  function handleClick() {
    onSort(nextSortState(column, currentSort));
  }

  const Icon =
    order === "asc" ? ArrowUp : order === "desc" ? ArrowDown : ChevronsUpDown;

  return (
    <button
      type="button"
      onClick={handleClick}
      aria-label={`${label} — ${t(stateAriaKey)}`}
      data-testid={testId ?? `column-header-${column}`}
      // Sort state for tests and E2E. Deliberately a data-* attribute, not
      // `aria-sort` — see the "Why there is no aria-sort" note above.
      data-sort-order={order ?? "none"}
      className={cn(
        // W11-C polish — Vercel-style header chip. Inline-flex chip with a
        // tight gutter that picks up a subtle muted background on hover so
        // the column reads as "click here to sort" without ambiguity (audit
        // O5 noted sortable headers had no hover affordance). Linear motion
        // tokens (150 ms ease-out-soft) match button / row transitions, and
        // the focus ring uses the same offset 2 + 2 px the rest of W11 uses.
        "group inline-flex items-center gap-1 rounded-sm px-1 -mx-1 py-0.5",
        "text-xs font-medium uppercase tracking-wider",
        "transition-colors duration-fast ease-out-soft",
        "hover:bg-muted hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        isActive ? "text-foreground" : "text-muted-foreground",
        className,
      )}
    >
      <span>{label}</span>
      <Icon
        aria-hidden
        className={cn(
          // The chevron stays half-opacity at rest so the header reads
          // typographic. On hover (group-hover) or when the column is the
          // active sort, it lifts to full opacity — making the sort
          // affordance visible the moment a pointer crosses the header.
          "h-3 w-3 shrink-0 transition-opacity duration-fast ease-out-soft",
          isActive
            ? "opacity-100"
            : "opacity-40 group-hover:opacity-80",
        )}
      />
    </button>
  );
}
