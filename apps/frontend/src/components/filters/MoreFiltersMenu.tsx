// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { CheckIcon, PlusIcon } from "@primer/octicons-react";
import { ActionList, ActionMenu } from "@primer/react";
import { useTranslation } from "react-i18next";

import { IconButton } from "@/components/primer/IconButton";

/**
 * MoreFiltersMenu — W9 #52 generic "+ Add filter" affordance.
 *
 * A findings toolbar conventionally pairs inline filter chips with a trailing
 * "+ More filters" dropdown listing facets the user can opt into. We were
 * already at audit score 4/5 with the inline chip + chart deep-link + URL
 * mirror; the missing piece is the dropdown that makes the *available* extra
 * filters discoverable.
 *
 * This primitive is generic (no domain coupling) so both the Components and
 * Vulnerabilities tabs can drive it from their own facet catalog. The dropdown
 * is purely a discovery affordance: clicking an option fires `onSelect` and
 * the parent decides what to do — typically "mount an inline filter UI for
 * this facet" which then surfaces as a new chip / control in the toolbar.
 *
 * CLAUDE.md "디자인 시스템": filters appear inline at the top of lists, no
 * modal filter dialogs. The button trigger is the same compact h-9 height as
 * the other toolbar controls; the popover uses `bg-popover` so it lays opaque
 * over the table below. Color is paired with the plus icon + label, never
 * color-only.
 */

export interface MoreFiltersMenuOption {
  /** Stable facet id (e.g. "license_category"). */
  id: string;
  /** Already-translated user-visible label for the option row. */
  label: string;
}

export interface MoreFiltersMenuProps {
  /** Catalog of facets the parent is willing to expose through this dropdown. */
  availableFilters: MoreFiltersMenuOption[];
  /**
   * Set of facet ids that are currently active (visible in the toolbar). The
   * dropdown shows a check next to each active row so a user clicking the
   * trigger again sees which filters they've already turned on.
   */
  activeFilterIds: Set<string>;
  /** Called with the clicked facet id. The parent mounts / unmounts the UI. */
  onSelect: (filterId: string) => void;
  /** Disables the trigger (e.g. read-only snapshot mode). */
  disabled?: boolean;
  /**
   * Base test id — defaults to `more-filters-trigger`. Set this to a
   * scope-specific value (e.g. `vulnerabilities-more-filters-trigger`) when
   * two MoreFiltersMenu instances co-exist on the same page.
   */
  testId?: string;
  /** Trigger width utility (matches the surrounding toolbar controls). */
  className?: string;
  /** Icon-only trigger for toolbars short of width; the name stays as the tooltip. */
  compact?: boolean;
}

export function MoreFiltersMenu({
  availableFilters,
  activeFilterIds,
  onSelect,
  disabled = false,
  testId = "more-filters-trigger",
  className,
  compact = false,
}: MoreFiltersMenuProps) {
  const { t } = useTranslation("common");

  if (availableFilters.length === 0) {
    // Nothing to discover → render nothing. Saves the user a click on an
    // empty popover and keeps the toolbar from acquiring a dead affordance.
    return null;
  }

  return (
    <ActionMenu>
      {compact ? (
        <ActionMenu.Anchor>
          <IconButton
            icon={PlusIcon}
            disabled={disabled}
            aria-label={t("filters.more_filters.trigger")}
            data-testid={testId}
            className={className}
          />
        </ActionMenu.Anchor>
      ) : (
        <ActionMenu.Button
          leadingVisual={PlusIcon}
          disabled={disabled}
          aria-label={t("filters.more_filters.aria")}
          data-testid={testId}
          className={className}
        >
          {t("filters.more_filters.trigger")}
        </ActionMenu.Button>
      )}
      <ActionMenu.Overlay width="small">
        <ActionList>
          {availableFilters.map((option) => (
            <ActionList.Item
              key={option.id}
              // Discovery is single-shot: the menu closes, and the parent
              // handles the toggle.
              onSelect={() => onSelect(option.id)}
              data-testid={`${testId}-option-${option.id}`}
              // Not `data-active`: Primer's list item sets that attribute itself.
              data-filter-active={activeFilterIds.has(option.id) ? "true" : "false"}
            >
              {option.label}
              {activeFilterIds.has(option.id) ? (
                <ActionList.TrailingVisual>
                  <CheckIcon size={16} />
                </ActionList.TrailingVisual>
              ) : null}
            </ActionList.Item>
          ))}
        </ActionList>
      </ActionMenu.Overlay>
    </ActionMenu>
  );
}
