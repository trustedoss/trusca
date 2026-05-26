import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

/**
 * MultiSelect — app-i18n checkbox-list dropdown.
 *
 * Replaces the project-detail filter toolbars' native `<select multiple>`
 * elements. The browser renders a native multi-select's collapsed widget in
 * the *OS* locale ("0개 선택됨" on a Korean OS even when the app language is
 * English), and stacks its options into a cramped scroll box. This control
 * fixes both: the trigger label comes from `common:multiselect.count_selected`
 * (app i18n) and the options are real checkbox rows in an opaque popover.
 *
 * Built on the existing Radix DropdownMenu primitive (already a dependency —
 * no new package) so we inherit keyboard navigation, focus management, and
 * aria-menu semantics. The dropdown content uses `bg-popover` (mapped in
 * tailwind.config) so it renders fully opaque over the list below.
 *
 * CLAUDE.md "디자인 시스템": design tokens only (no hex literals), color is
 * never the only signal (each row pairs its label with a visible checkmark +
 * `aria-checked`), filters stay inline at the top of lists (no modal).
 */

export interface MultiSelectOption {
  value: string;
  /** Already-translated, user-visible label for the option row. */
  label: string;
}

export interface MultiSelectProps {
  options: MultiSelectOption[];
  /** Currently selected option values. */
  selected: string[];
  /** Called with the next selection array whenever a row is toggled. */
  onChange: (next: string[]) => void;
  /** Trigger text when nothing is selected (e.g. "All"). */
  placeholder?: string;
  /** Accessible name for the trigger button (aria-label). */
  label?: string;
  /**
   * Base test id. Applied to the trigger; option rows get `<testId>-option`
   * with a `data-value`, so callers can keep their existing toolbar testids.
   */
  testId?: string;
  /** Disables the whole control (e.g. read-only snapshot mode). */
  disabled?: boolean;
  /** Width utility for the trigger (matches the old `w-40` / `w-44`). */
  className?: string;
  /** id wiring for an external `<label htmlFor>`. */
  id?: string;
}

/**
 * Toggle one value in/out of the selection, preserving the original option
 * order so the resulting array is deterministic (important for URL params and
 * test assertions like `{ severity: ["critical"] }`).
 */
function toggleValue(
  options: MultiSelectOption[],
  selected: string[],
  value: string,
): string[] {
  const set = new Set(selected);
  if (set.has(value)) {
    set.delete(value);
  } else {
    set.add(value);
  }
  return options.map((o) => o.value).filter((v) => set.has(v));
}

export function MultiSelect({
  options,
  selected,
  onChange,
  placeholder,
  label,
  testId,
  disabled = false,
  className,
  id,
}: MultiSelectProps) {
  const { t } = useTranslation();
  const count = selected.length;
  const triggerText =
    count > 0
      ? t("common:multiselect.count_selected", { count })
      : (placeholder ?? t("common:multiselect.all"));

  return (
    <DropdownMenuPrimitive.Root>
      <DropdownMenuPrimitive.Trigger asChild>
        <button
          type="button"
          id={id}
          disabled={disabled}
          aria-label={label}
          data-testid={testId}
          className={cn(
            "mt-1 flex h-9 items-center justify-between gap-2 rounded-md border border-input bg-background px-2 text-sm",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
            "disabled:cursor-not-allowed disabled:opacity-50",
            className,
          )}
        >
          <span className="truncate">{triggerText}</span>
          <ChevronDown
            className="h-4 w-4 shrink-0 text-muted-foreground"
            aria-hidden
          />
        </button>
      </DropdownMenuPrimitive.Trigger>
      <DropdownMenuPrimitive.Portal>
        <DropdownMenuPrimitive.Content
          align="start"
          sideOffset={4}
          // `bg-popover` (opaque) keeps the dropdown from showing the list
          // rows bleeding through. min-w matches the widest old select.
          className={cn(
            "z-50 min-w-[10rem] overflow-hidden rounded-md border bg-popover p-1 text-popover-foreground shadow-md",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
          )}
        >
          {options.map((option) => {
            const isSelected = selected.includes(option.value);
            return (
              <DropdownMenuPrimitive.CheckboxItem
                key={option.value}
                checked={isSelected}
                // Keep the menu open so the user can toggle several at once.
                onSelect={(event) => event.preventDefault()}
                onCheckedChange={() =>
                  onChange(toggleValue(options, selected, option.value))
                }
                data-testid={testId ? `${testId}-option` : undefined}
                data-value={option.value}
                className={cn(
                  "relative flex cursor-pointer select-none items-center gap-2 rounded-sm py-1.5 pl-2 pr-2 text-sm outline-none transition-colors",
                  "focus:bg-accent focus:text-accent-foreground data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground",
                  "data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
                )}
              >
                <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border border-input">
                  {isSelected ? (
                    <Check className="h-3.5 w-3.5" aria-hidden />
                  ) : null}
                </span>
                <span className="truncate">{option.label}</span>
              </DropdownMenuPrimitive.CheckboxItem>
            );
          })}
          {count > 0 ? (
            <>
              <DropdownMenuPrimitive.Separator className="-mx-1 my-1 h-px bg-muted" />
              <DropdownMenuPrimitive.Item
                onSelect={(event) => {
                  event.preventDefault();
                  onChange([]);
                }}
                data-testid={testId ? `${testId}-clear` : undefined}
                className={cn(
                  "relative flex cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-sm text-muted-foreground outline-none transition-colors",
                  "focus:bg-accent focus:text-accent-foreground data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground",
                )}
              >
                {t("common:multiselect.clear")}
              </DropdownMenuPrimitive.Item>
            </>
          ) : null}
        </DropdownMenuPrimitive.Content>
      </DropdownMenuPrimitive.Portal>
    </DropdownMenuPrimitive.Root>
  );
}
