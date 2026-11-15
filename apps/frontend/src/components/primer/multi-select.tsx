// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { ActionList, ActionMenu } from "@primer/react";
import { useTranslation } from "react-i18next";

/**
 * MultiSelect - app-i18n checkbox-list dropdown.
 *
 * Replaces the project-detail filter toolbars' native `<select multiple>`
 * elements. The browser renders a native multi-select's collapsed widget in
 * the *OS* locale ("0개 선택됨" on a Korean OS even when the app language is
 * English), and stacks its options into a cramped scroll box. This control
 * fixes both: the trigger label comes from `common:multiselect.count_selected`
 * (app i18n) and the options are real checkbox rows in an opaque popover.
 *
 * Built on the existing Radix DropdownMenu primitive (already a dependency -
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
    <ActionMenu>
      <ActionMenu.Button
        id={id}
        disabled={disabled}
        aria-label={label}
        data-testid={testId}
        className={className}
        alignContent="start"
      >
        <span className="truncate">{triggerText}</span>
      </ActionMenu.Button>
      <ActionMenu.Overlay width="auto" minWidth="small">
        <ActionList selectionVariant="multiple">
          {options.map((option) => (
            <ActionList.Item
              key={option.value}
              selected={selected.includes(option.value)}
              // Keep the menu open so several can be toggled in one visit.
              onSelect={(event) => {
                event.preventDefault();
                onChange(toggleValue(options, selected, option.value));
              }}
              data-testid={testId ? `${testId}-option` : undefined}
              data-value={option.value}
            >
              {option.label}
            </ActionList.Item>
          ))}
          {count > 0 ? (
            <>
              <ActionList.Divider />
              <ActionList.Item
                onSelect={(event) => {
                  event.preventDefault();
                  onChange([]);
                }}
                data-testid={testId ? `${testId}-clear` : undefined}
              >
                {t("common:multiselect.clear")}
              </ActionList.Item>
            </>
          ) : null}
        </ActionList>
      </ActionMenu.Overlay>
    </ActionMenu>
  );
}
