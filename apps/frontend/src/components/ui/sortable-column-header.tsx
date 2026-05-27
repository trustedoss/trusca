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
 *   - Rendered as a `<button>` inside the `<th>` for full keyboard support.
 *   - `aria-sort` mirrors the DOM contract ("none" / "ascending" / "descending").
 *   - Icon swaps so the cue isn't color-only.
 *
 * The component does not manage focus or table semantics; the caller is
 * responsible for wrapping headers in the appropriate table element.
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
  const ariaSort: "none" | "ascending" | "descending" =
    order === "asc"
      ? "ascending"
      : order === "desc"
        ? "descending"
        : "none";
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
      aria-sort={ariaSort}
      aria-label={`${label} — ${t(stateAriaKey)}`}
      data-testid={testId ?? `column-header-${column}`}
      data-sort-order={order ?? "none"}
      className={cn(
        "inline-flex items-center gap-1 text-xs font-medium uppercase tracking-wide",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
        isActive
          ? "text-foreground"
          : "text-muted-foreground hover:text-foreground",
        className,
      )}
    >
      <span>{label}</span>
      <Icon
        aria-hidden
        className={cn(
          "h-3 w-3 shrink-0",
          isActive ? "opacity-100" : "opacity-50",
        )}
      />
    </button>
  );
}
