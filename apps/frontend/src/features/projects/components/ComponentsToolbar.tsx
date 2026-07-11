import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  ColumnsPicker,
  type ColumnsPickerColumn,
} from "@/components/filters/ColumnsPicker";
import {
  MoreFiltersMenu,
  type MoreFiltersMenuOption,
} from "@/components/filters/MoreFiltersMenu";
import { Input } from "@/components/ui/input";
import { MultiSelect } from "@/components/ui/multi-select";
import type {
  ComponentSeverity,
  DependencyScopeFilter,
  LicenseCategoryName,
} from "@/features/projects/api/projectDetailApi";
import { cn } from "@/lib/utils";

/**
 * W9 #52 — facet ids the "+ Add filter" dropdown exposes on the Components
 * tab. Severity + license category are populated today via the Overview
 * chart deep-links (#16) but the toolbar exposed no inline UI for either —
 * a triager who didn't click the chart would not know they could filter by
 * these. The dropdown surfaces both as opt-in MultiSelects.
 */
export type ComponentsExtraFilter = "severity" | "license_category";

/**
 * W9 #52 — severity option set for the Components tab. Mirrors the parent's
 * `VALID_SEVERITY` tuple so URL-driven values stay round-trip stable.
 */
const COMPONENT_SEVERITY_OPTIONS: ComponentSeverity[] = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
  "none",
];

/**
 * W9 #52 — license-category options. Same shape as Vulnerabilities.
 */
const COMPONENT_LICENSE_CATEGORY_OPTIONS: LicenseCategoryName[] = [
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
];

/**
 * ComponentsToolbar — Phase 3 PR #10, updated in W4-B #17.
 *
 * Inline filter row above the virtualized component list. CLAUDE.md
 * "디자인 시스템" — filters appear inline at the top of lists, no modal
 * filter dialogs.
 *
 * W4-B #17 removed two control families:
 *   - Severity + License MultiSelect drops: replaced by the Overview chart
 *     deep-links (#16) + the standalone `ActiveFilterChips` row above the
 *     table. The URL params (`?severity=`, `?license_category=`) are still
 *     honoured by the parent `ComponentsTab`; the toolbar simply no longer
 *     hosts a "type into a dropdown" UI for them.
 *   - Sort + Order <select> drops: replaced by inline `SortableColumnHeader`
 *     primitives on the column heads in the table itself.
 *
 * What stays here: Search, Dependency-type segmented control, Usage multi
 * select. They have no equivalent affordance elsewhere on the page.
 */

/**
 * W2 #31 — BD-style "Usage" facet options. Mirrors the backend's accepted
 * values for ``?dependency_scope=``; ``unspecified`` selects the NULL-scope
 * bucket (cdxgen often produces no scope on edges).
 */
export const USAGE_OPTIONS: DependencyScopeFilter[] = [
  "required",
  "optional",
  "unspecified",
];

/**
 * W2 #31 — Dependency-type segmented control values. ``null`` means "All"
 * (no `?direct=` on the wire), ``true`` keeps only direct, ``false`` only
 * transitive (and the depth-null bucket).
 */
const DEPENDENCY_TYPE_OPTIONS: { value: boolean | null; key: string }[] = [
  { value: null, key: "all" },
  { value: true, key: "direct" },
  { value: false, key: "transitive" },
];

export interface ComponentsToolbarProps {
  search: string;
  onSearchChange: (value: string) => void;
  /** Dependency-type 3-state (null/true/false). */
  direct: boolean | null;
  onDirectChange: (value: boolean | null) => void;
  /** BD-style "Usage" multi-select. */
  dependencyScope: DependencyScopeFilter[];
  onDependencyScopeChange: (value: DependencyScopeFilter[]) => void;
  /** Phase M — EOL-only toggle (`?eol=true` when on; off = no opinion). */
  eolOnly: boolean;
  onEolOnlyChange: (value: boolean) => void;
  /**
   * W9 #52 — current severity selection (mirrors parent state). When the
   * user mounts the severity facet via "+ Add filter", the toolbar renders
   * a MultiSelect bound to this prop.
   */
  severity: ComponentSeverity[];
  onSeverityChange: (value: ComponentSeverity[]) => void;
  /**
   * W9 #52 — current license-category selection (mirrors parent state).
   */
  licenseCategory: LicenseCategoryName[];
  onLicenseCategoryChange: (value: LicenseCategoryName[]) => void;
  /**
   * W9 #52 — facets the user has opted into via "+ Add filter".
   */
  mountedExtraFilters: Set<ComponentsExtraFilter>;
  onMountExtraFilter: (filter: ComponentsExtraFilter) => void;
  onUnmountExtraFilter: (filter: ComponentsExtraFilter) => void;
  /**
   * W9 #52 — column-picker catalog + visibility. Parents own the state so
   * row code can gate cell rendering on it.
   */
  columnsCatalog: ColumnsPickerColumn[];
  visibleColumns: Set<string>;
  onVisibleColumnsChange: (next: Set<string>) => void;
  columnsStorageKey: string;
  className?: string;
}

export function ComponentsToolbar({
  search,
  onSearchChange,
  direct,
  onDirectChange,
  dependencyScope,
  onDependencyScopeChange,
  eolOnly,
  onEolOnlyChange,
  severity,
  onSeverityChange,
  licenseCategory,
  onLicenseCategoryChange,
  mountedExtraFilters,
  onMountExtraFilter,
  onUnmountExtraFilter,
  columnsCatalog,
  visibleColumns,
  onVisibleColumnsChange,
  columnsStorageKey,
  className,
}: ComponentsToolbarProps) {
  const { t } = useTranslation("project_detail");

  // W9 #52 — see VulnerabilitiesToolbar for the active-id detection rationale.
  const availableExtraFilters: MoreFiltersMenuOption[] = [
    { id: "severity", label: t("extra_filters.severity_label") },
    {
      id: "license_category",
      label: t("extra_filters.license_category_label"),
    },
  ];
  const activeFilterIds = new Set<string>();
  if (mountedExtraFilters.has("severity") || severity.length > 0) {
    activeFilterIds.add("severity");
  }
  if (
    mountedExtraFilters.has("license_category") ||
    licenseCategory.length > 0
  ) {
    activeFilterIds.add("license_category");
  }

  function handleMoreFiltersSelect(filterId: string) {
    if (filterId === "severity" || filterId === "license_category") {
      const isMounted = mountedExtraFilters.has(filterId);
      const hasValue =
        filterId === "severity"
          ? severity.length > 0
          : licenseCategory.length > 0;
      if (isMounted && !hasValue) {
        onUnmountExtraFilter(filterId);
      } else {
        onMountExtraFilter(filterId);
      }
    }
  }

  const showSeverityFacet =
    mountedExtraFilters.has("severity") || severity.length > 0;
  const showLicenseFacet =
    mountedExtraFilters.has("license_category") || licenseCategory.length > 0;
  return (
    <div
      className={cn(
        "flex flex-col gap-3 border-b bg-background px-4 py-3 lg:flex-row lg:items-end lg:gap-4",
        className,
      )}
      data-testid="components-toolbar"
    >
      <div className="flex-1">
        <label
          htmlFor="components-search"
          className="block text-xs font-medium text-muted-foreground"
        >
          {t("components.toolbar.search_label")}
        </label>
        <Input
          id="components-search"
          type="search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder={t("components.toolbar.search_placeholder")}
          data-testid="components-search"
          className="mt-1 h-9"
        />
      </div>

      <div
        className="flex flex-col"
        data-testid="components-dependency-type-filter"
      >
        <span className="text-xs font-medium text-muted-foreground">
          {t("components.toolbar.dependency_type_label")}
        </span>
        <div
          role="group"
          aria-label={t("components.toolbar.dependency_type_label")}
          className="mt-1 inline-flex h-9 items-stretch overflow-hidden rounded-md border border-input bg-background"
        >
          {DEPENDENCY_TYPE_OPTIONS.map((opt, idx) => {
            const isActive = direct === opt.value;
            return (
              <button
                key={opt.key}
                type="button"
                aria-pressed={isActive}
                data-testid={`components-dependency-type-${opt.key}`}
                data-active={isActive ? "true" : "false"}
                onClick={() => onDirectChange(opt.value)}
                className={cn(
                  // W11-F polish — segmented control hovers / active-state
                  // colour swap glides on the W11-A 150 ms ease-out-soft so
                  // toggling between dependency-type pills no longer flips.
                  "px-3 text-xs font-medium transition-colors duration-fast ease-out-soft",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                  idx > 0 && "border-l border-input",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                {t(`components.toolbar.dependency_type.${opt.key}`)}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="components-usage-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("components.toolbar.usage_label")}
        </label>
        <MultiSelect
          id="components-usage-filter"
          testId="components-usage-filter"
          className="w-40"
          label={t("components.toolbar.usage_label")}
          options={USAGE_OPTIONS.map((opt) => ({
            value: opt,
            label: t(`components.toolbar.usage.${opt}`),
          }))}
          selected={dependencyScope}
          onChange={(next) =>
            onDependencyScopeChange(next as DependencyScopeFilter[])
          }
        />
      </div>

      {/* Phase M — EOL-only toggle. A single pressed-state pill (not a
          3-state segment): "on" narrows to past-end-of-life components,
          "off" means no opinion — `?eol=false` is never emitted from here. */}
      <div className="flex flex-col">
        <span className="text-xs font-medium text-muted-foreground">
          {t("components.toolbar.eol_label")}
        </span>
        <button
          type="button"
          aria-pressed={eolOnly}
          data-testid="components-eol-filter"
          data-active={eolOnly ? "true" : "false"}
          onClick={() => onEolOnlyChange(!eolOnly)}
          className={cn(
            "mt-1 inline-flex h-9 items-center rounded-md border border-input px-3 text-xs font-medium",
            "transition-colors duration-fast ease-out-soft",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
            eolOnly
              ? "bg-primary text-primary-foreground"
              : "bg-background text-muted-foreground hover:bg-muted",
          )}
        >
          {t("components.toolbar.eol_only")}
        </button>
      </div>

      {/* W9 #52 — optional severity facet, mount-on-demand. */}
      {showSeverityFacet ? (
        <div
          className="flex flex-col"
          data-testid="components-severity-facet"
        >
          <div className="flex items-center gap-1">
            <label
              htmlFor="components-severity-filter"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("extra_filters.severity_label")}
            </label>
            <button
              type="button"
              data-testid="components-severity-facet-remove"
              aria-label={t("extra_filters.remove_aria", {
                label: t("extra_filters.severity_label"),
              })}
              onClick={() => {
                onSeverityChange([]);
                onUnmountExtraFilter("severity");
              }}
              className="ml-auto inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground transition-colors duration-fast ease-out-soft hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <X className="h-3 w-3" aria-hidden />
            </button>
          </div>
          <MultiSelect
            id="components-severity-filter"
            testId="components-severity-filter"
            className="w-44"
            label={t("extra_filters.severity_label")}
            options={COMPONENT_SEVERITY_OPTIONS.map((opt) => ({
              value: opt,
              label: t(`severity.${opt}`),
            }))}
            selected={severity}
            onChange={(next) => onSeverityChange(next as ComponentSeverity[])}
          />
        </div>
      ) : null}

      {/* W9 #52 — optional license category facet, mount-on-demand. */}
      {showLicenseFacet ? (
        <div
          className="flex flex-col"
          data-testid="components-license-category-facet"
        >
          <div className="flex items-center gap-1">
            <label
              htmlFor="components-license-category-filter"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("extra_filters.license_category_label")}
            </label>
            <button
              type="button"
              data-testid="components-license-category-facet-remove"
              aria-label={t("extra_filters.remove_aria", {
                label: t("extra_filters.license_category_label"),
              })}
              onClick={() => {
                onLicenseCategoryChange([]);
                onUnmountExtraFilter("license_category");
              }}
              className="ml-auto inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground transition-colors duration-fast ease-out-soft hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <X className="h-3 w-3" aria-hidden />
            </button>
          </div>
          <MultiSelect
            id="components-license-category-filter"
            testId="components-license-category-filter"
            className="w-44"
            label={t("extra_filters.license_category_label")}
            options={COMPONENT_LICENSE_CATEGORY_OPTIONS.map((opt) => ({
              value: opt,
              label: t(`license_category.${opt}`),
            }))}
            selected={licenseCategory}
            onChange={(next) =>
              onLicenseCategoryChange(next as LicenseCategoryName[])
            }
          />
        </div>
      ) : null}

      <div className="flex flex-col lg:ml-auto">
        <span className="invisible text-xs font-medium" aria-hidden>
          &nbsp;
        </span>
        <MoreFiltersMenu
          availableFilters={availableExtraFilters}
          activeFilterIds={activeFilterIds}
          onSelect={handleMoreFiltersSelect}
          testId="components-more-filters-trigger"
        />
      </div>

      <div className="flex flex-col">
        <span className="invisible text-xs font-medium" aria-hidden>
          &nbsp;
        </span>
        <ColumnsPicker
          columns={columnsCatalog}
          visibleColumns={visibleColumns}
          onChange={onVisibleColumnsChange}
          storageKey={columnsStorageKey}
          testId="components-columns-picker-trigger"
        />
      </div>
    </div>
  );
}
