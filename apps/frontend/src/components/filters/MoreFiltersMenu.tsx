import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { Check, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

/**
 * MoreFiltersMenu — W9 #52 generic "+ Add filter" affordance.
 *
 * The Mend findings UI (audit reference image
 * `docs/ux/screens/competitors/mend/mend-findings-image-20260426-082111.png`)
 * exposes inline filter chips + a trailing "+ More Filters" dropdown that
 * lists facets the user can opt into. We were already at audit score 4/5 with
 * the inline chip + chart deep-link + URL mirror; the missing piece is the
 * dropdown that makes the *available* extra filters discoverable.
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
 * over the table below. Color is paired with the Plus icon + label — never
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
}

export function MoreFiltersMenu({
  availableFilters,
  activeFilterIds,
  onSelect,
  disabled = false,
  testId = "more-filters-trigger",
  className,
}: MoreFiltersMenuProps) {
  const { t } = useTranslation("common");

  if (availableFilters.length === 0) {
    // Nothing to discover → render nothing. Saves the user a click on an
    // empty popover and keeps the toolbar from acquiring a dead affordance.
    return null;
  }

  return (
    <DropdownMenuPrimitive.Root>
      <DropdownMenuPrimitive.Trigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={t("filters.more_filters.aria")}
          data-testid={testId}
          className={cn(
            "inline-flex h-9 items-center gap-1 rounded-md border border-input bg-background px-3 text-xs font-medium text-muted-foreground",
            "hover:bg-muted hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
            "disabled:cursor-not-allowed disabled:opacity-50",
            className,
          )}
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          <span>{t("filters.more_filters.trigger")}</span>
        </button>
      </DropdownMenuPrimitive.Trigger>
      <DropdownMenuPrimitive.Portal>
        <DropdownMenuPrimitive.Content
          align="start"
          sideOffset={4}
          className={cn(
            "z-50 min-w-[12rem] overflow-hidden rounded-md border bg-popover p-1 text-popover-foreground shadow-md",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
          )}
        >
          {availableFilters.map((option) => {
            const isActive = activeFilterIds.has(option.id);
            return (
              <DropdownMenuPrimitive.Item
                key={option.id}
                onSelect={(event) => {
                  // Let the menu close naturally — discovery affordances are
                  // single-shot. The parent handles the toggle semantics.
                  event.preventDefault();
                  onSelect(option.id);
                }}
                data-testid={`${testId}-option-${option.id}`}
                data-active={isActive ? "true" : "false"}
                className={cn(
                  "relative flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none transition-colors duration-fast ease-out-soft",
                  "focus:bg-accent focus:text-accent-foreground data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground",
                  isActive && "text-muted-foreground",
                )}
              >
                <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                  {isActive ? (
                    <Check className="h-3.5 w-3.5" aria-hidden />
                  ) : null}
                </span>
                <span className="truncate">{option.label}</span>
              </DropdownMenuPrimitive.Item>
            );
          })}
        </DropdownMenuPrimitive.Content>
      </DropdownMenuPrimitive.Portal>
    </DropdownMenuPrimitive.Root>
  );
}
