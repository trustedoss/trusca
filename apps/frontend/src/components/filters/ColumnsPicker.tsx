import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { Check, Columns3 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

/**
 * ColumnsPicker — W9 #52 generic "Columns" affordance.
 *
 * The Mend findings UI surfaces a Columns checkbox-list dropdown on the
 * right edge of the toolbar that lets a triager hide / show table columns
 * (audit reference image
 * `docs/ux/screens/competitors/mend/mend-findings-image-20260426-082111.png`).
 * This primitive mirrors that pattern — opaque popover, one checkbox row per
 * column, "required" columns rendered disabled+checked so the user can't hide
 * the row-identifying ones (CVE ID / Component / Severity).
 *
 * Selection is persisted to `localStorage` keyed on the caller's `storageKey`
 * (e.g. `column-visibility:vulnerabilities`) so a user's column preference
 * survives reload. Per-tab storage avoids the Components tab inheriting the
 * Vulnerabilities tab's hidden columns and vice-versa.
 *
 * CLAUDE.md "디자인 시스템": design tokens only, the icon + label pairing
 * keeps the affordance discoverable without color signal. The button itself
 * sits at h-9 to match the surrounding toolbar controls.
 */

export interface ColumnsPickerColumn {
  /** Stable column id (e.g. "cvss"). Used as the React key + localStorage key. */
  id: string;
  /** Already-translated user-visible column label. */
  label: string;
  /**
   * Columns the user must never hide (row identity / severity signal).
   * Required columns render as disabled checkboxes that always read true.
   */
  required?: boolean;
}

export interface ColumnsPickerProps {
  /** Catalog of columns the parent table is willing to expose. */
  columns: ColumnsPickerColumn[];
  /**
   * The currently-visible column ids. Required columns must always be in the
   * set — parents that hold visibility in their own state should seed it via
   * `loadInitialVisibility` below.
   */
  visibleColumns: Set<string>;
  /** Called with the next visibility set whenever a row is toggled. */
  onChange: (next: Set<string>) => void;
  /**
   * Per-tab localStorage key (e.g. `column-visibility:vulnerabilities`). When
   * supplied, the picker writes the visible set on every change. The parent
   * is responsible for reading on mount via `loadInitialVisibility`.
   */
  storageKey?: string;
  /**
   * Base test id — defaults to `columns-picker-trigger`. Override when two
   * pickers co-exist on the same page.
   */
  testId?: string;
  /** Disables the trigger (e.g. read-only snapshot mode). */
  disabled?: boolean;
  /** Trigger width utility. */
  className?: string;
}

/**
 * Hydrate a visibility set from localStorage. Required column ids are always
 * unioned in so a stale localStorage entry from a previous schema can't hide
 * an identity column. Falls back to "all visible" when the key is missing or
 * malformed — the user gets the default surface, not a blank table.
 *
 * Exported so the parent tab can seed its own visibility state on mount; the
 * picker itself doesn't own the state because the parent needs to read it to
 * decide which cells to render.
 */
export function loadInitialVisibility(
  storageKey: string,
  columns: ColumnsPickerColumn[],
): Set<string> {
  const required = new Set(
    columns.filter((c) => c.required).map((c) => c.id),
  );
  if (typeof window === "undefined" || !window.localStorage) {
    return new Set(columns.map((c) => c.id));
  }
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) {
      return new Set(columns.map((c) => c.id));
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return new Set(columns.map((c) => c.id));
    }
    // Filter to ids still in the column catalog so a schema drift doesn't
    // resurrect a removed column id. Always re-union required ids.
    const known = new Set(columns.map((c) => c.id));
    const next = new Set<string>();
    for (const id of parsed) {
      if (typeof id === "string" && known.has(id)) next.add(id);
    }
    for (const id of required) next.add(id);
    return next;
  } catch {
    // localStorage throws in private-mode Safari and on quota errors. Fall
    // back to "show all" so the user is never stuck with a broken header.
    return new Set(columns.map((c) => c.id));
  }
}

/**
 * Write the visibility set to localStorage. No-ops in non-browser environments
 * (Vitest jsdom does provide localStorage, so the unit tests can still observe
 * the writes).
 */
export function saveVisibility(storageKey: string, visible: Set<string>): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(Array.from(visible)));
  } catch {
    /* private-mode / quota — silently ignore. */
  }
}

export function ColumnsPicker({
  columns,
  visibleColumns,
  onChange,
  storageKey,
  testId = "columns-picker-trigger",
  disabled = false,
  className,
}: ColumnsPickerProps) {
  const { t } = useTranslation("common");
  // Track the menu's open state so we can rebuild it when columns change. The
  // popover itself is uncontrolled by Radix in normal use; this is here only
  // so a parent driving visibility from outside can still react to changes.
  const [, forceRender] = useState(0);
  useEffect(() => {
    forceRender((n) => n + 1);
  }, [visibleColumns]);

  const handleToggle = useCallback(
    (columnId: string, column: ColumnsPickerColumn) => {
      if (column.required) return;
      const next = new Set(visibleColumns);
      if (next.has(columnId)) {
        next.delete(columnId);
      } else {
        next.add(columnId);
      }
      // Always re-union required ids so a future "Hide all"-style action can
      // never strip the identity columns.
      for (const c of columns) {
        if (c.required) next.add(c.id);
      }
      if (storageKey) {
        saveVisibility(storageKey, next);
      }
      onChange(next);
    },
    [columns, onChange, storageKey, visibleColumns],
  );

  const hint = useMemo(
    () => t("filters.columns.required_hint"),
    [t],
  );

  return (
    <DropdownMenuPrimitive.Root>
      <DropdownMenuPrimitive.Trigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={t("filters.columns.aria")}
          data-testid={testId}
          className={cn(
            "inline-flex h-9 items-center gap-1 rounded-md border border-input bg-background px-3 text-xs font-medium text-muted-foreground",
            "hover:bg-muted hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
            "disabled:cursor-not-allowed disabled:opacity-50",
            className,
          )}
        >
          <Columns3 className="h-3.5 w-3.5" aria-hidden />
          <span>{t("filters.columns.trigger")}</span>
        </button>
      </DropdownMenuPrimitive.Trigger>
      <DropdownMenuPrimitive.Portal>
        <DropdownMenuPrimitive.Content
          align="end"
          sideOffset={4}
          className={cn(
            "z-50 min-w-[14rem] overflow-hidden rounded-md border bg-popover p-1 text-popover-foreground shadow-md",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
          )}
        >
          {columns.map((column) => {
            const isVisible =
              column.required || visibleColumns.has(column.id);
            return (
              <DropdownMenuPrimitive.CheckboxItem
                key={column.id}
                checked={isVisible}
                onSelect={(event) => event.preventDefault()}
                onCheckedChange={() => handleToggle(column.id, column)}
                disabled={column.required}
                data-testid={`${testId.replace(
                  /-trigger$/,
                  "",
                )}-option-${column.id}`}
                data-required={column.required ? "true" : "false"}
                data-visible={isVisible ? "true" : "false"}
                className={cn(
                  "relative flex cursor-pointer select-none items-center gap-2 rounded-sm py-1.5 pl-2 pr-2 text-sm outline-none transition-colors",
                  "focus:bg-accent focus:text-accent-foreground data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground",
                  "data-[disabled]:pointer-events-none data-[disabled]:opacity-60",
                )}
              >
                <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border border-input">
                  {isVisible ? (
                    <Check className="h-3.5 w-3.5" aria-hidden />
                  ) : null}
                </span>
                <span className="flex flex-1 items-center justify-between gap-2">
                  <span className="truncate">{column.label}</span>
                  {column.required ? (
                    <span
                      className="shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground"
                      data-testid={`${testId.replace(
                        /-trigger$/,
                        "",
                      )}-required-hint-${column.id}`}
                    >
                      {hint}
                    </span>
                  ) : null}
                </span>
              </DropdownMenuPrimitive.CheckboxItem>
            );
          })}
        </DropdownMenuPrimitive.Content>
      </DropdownMenuPrimitive.Portal>
    </DropdownMenuPrimitive.Root>
  );
}
