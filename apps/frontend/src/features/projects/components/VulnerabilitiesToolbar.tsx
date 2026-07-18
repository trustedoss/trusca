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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MultiSelect } from "@/components/ui/multi-select";
import type {
  LicenseCategoryName,
  TeamScopedRole,
} from "@/features/projects/api/projectDetailApi";
import type {
  ReachabilityFilter,
  VulnFindingStatus,
  VulnSeverity,
  VulnerabilitySortKey,
} from "@/features/projects/api/vulnerabilitiesApi";
import { VexExportMenu } from "@/features/projects/components/VexExportMenu";
import { VexImportDialog } from "@/features/projects/components/VexImportDialog";
import { ALL_VULNERABILITY_STATUSES } from "@/features/projects/lib/vulnerabilityTransitions";
import { cn } from "@/lib/utils";

/**
 * W9 #52 — facet ids the "+ Add filter" dropdown exposes on the
 * Vulnerabilities tab. Each id maps to one mount-on-demand MultiSelect; the
 * dropdown surfaces only facets that currently exist in chip form (severity
 * + license category — populated via the Overview chart deep-links / VEX
 * suppressions) so a user discovers they can also filter by these without
 * having to click the chart first.
 */
export type VulnerabilitiesExtraFilter = "severity" | "license_category";

/**
 * W9-#53 — grouping mode of the Vulnerabilities list. `"flat"` is the paginated
 * findings table; `"upgrade"` swaps it for the whole-project minimum-safe-
 * upgrade clusters (not paginated / not filtered), so the flat-list-only
 * controls (sort, status, filters, columns) are hidden in that mode.
 */
export type VulnerabilitiesGroupByMode = "flat" | "upgrade";

const GROUP_BY_OPTIONS: { value: VulnerabilitiesGroupByMode; key: string }[] = [
  { value: "flat", key: "flat" },
  { value: "upgrade", key: "upgrade" },
];

/**
 * VulnerabilitiesToolbar — Phase 3 PR #11, updated in W4-B #19.
 *
 * Inline filter row above the virtualized vulnerabilities list. Mirrors the
 * shape of `ComponentsToolbar` (CLAUDE.md "디자인 시스템": filters appear
 * inline at the top of lists, no modal filter dialogs).
 *
 * W4-B #19 removed the severity / license MultiSelect drops and the
 * sort / order <select> controls. Severity + license are now driven by the
 * Overview chart deep-links (#16) and visualized via the standalone
 * `ActiveFilterChips` row in the parent tab. Sort is in the column headers
 * themselves (SortableColumnHeader primitive).
 *
 * What stays here: Search, Status MultiSelect, Reachability filter, EPSS
 * threshold, VEX-suppressed toggle, VEX export/import + PDF download.
 */

export const STATUS_OPTIONS: VulnFindingStatus[] = [
  ...ALL_VULNERABILITY_STATUSES,
];

/**
 * Reachability filter dropdown options (v2.3 r2). `""` is the "any" / off state;
 * the three tokens map to the backend's `?reachable=` parameter.
 */
export const REACHABLE_OPTIONS: ReachabilityFilter[] = [
  "true",
  "false",
  "unknown",
];

/**
 * KEV feature — sort keys offered by the toolbar's sort select. Column sorts
 * moved to the headers in W4-B #19, but the composite `priority` ranking
 * (KEV → severity → EPSS) is not a column, so the select returns as the home
 * for it — listing the column keys too so the select always reflects the
 * current sort (single `sort` state shared with the headers).
 */
export const SORT_OPTIONS: VulnerabilitySortKey[] = [
  "priority",
  "severity",
  "cvss",
  "epss",
  "reachable",
  "component",
  "status",
  "discovered_at",
];

/** i18n label key per sort option — column sorts reuse the column labels. */
const SORT_OPTION_LABEL_KEY: Record<VulnerabilitySortKey, string> = {
  priority: "vulnerabilities.toolbar.sort_option.priority",
  severity: "vulnerabilities.column.severity",
  cvss: "vulnerabilities.column.cvss",
  epss: "vulnerabilities.column.epss",
  reachable: "vulnerabilities.column.reachable",
  component: "vulnerabilities.column.component",
  status: "vulnerabilities.column.status",
  discovered_at: "vulnerabilities.column.discovered",
};

export interface VulnerabilitiesToolbarProps {
  /**
   * W9-#53 — current grouping mode. `"flat"` renders every filter/sort/column
   * control; `"upgrade"` hides them (the grouped view is whole-project and not
   * paginated) leaving only the group-by segmented control.
   */
  groupBy: VulnerabilitiesGroupByMode;
  onGroupByChange: (value: VulnerabilitiesGroupByMode) => void;
  search: string;
  onSearchChange: (value: string) => void;
  /**
   * Current sort key (KEV feature). Shared state with the column headers —
   * clicking a header updates the select, and vice versa. `"priority"` is
   * the default (KEV → severity → EPSS).
   */
  sort: VulnerabilitySortKey;
  /** Called with the picked key; the parent resets direction to desc. */
  onSortKeyChange: (value: VulnerabilitySortKey) => void;
  status: VulnFindingStatus[];
  onStatusChange: (value: VulnFindingStatus[]) => void;
  /**
   * EPSS threshold (0–1) or `null` for "no threshold". Keeps findings with
   * `epss_score >= minEpss` and drops NULL-EPSS rows (v2.1).
   */
  minEpss: number | null;
  onMinEpssChange: (value: number | null) => void;
  /**
   * Tri-state reachability filter (v2.3 r2): `"true"` / `"false"` / `"unknown"`
   * or `null` for "any". Backs the `?reachable=` query parameter.
   */
  reachable: ReachabilityFilter | null;
  onReachableChange: (value: ReachabilityFilter | null) => void;
  /**
   * Keep only findings whose status was driven by a VEX import
   * (`analysis_source === "vex_import"`), v2.1 A3. Client-side narrowing of the
   * current page since the backend has no dedicated source filter yet.
   */
  vexSuppressedOnly: boolean;
  onVexSuppressedOnlyChange: (value: boolean) => void;
  /** Project id (for the VEX import/export controls). */
  projectId: string;
  /** Project name (download filename fallback). */
  projectName?: string | null;
  /** The actor's project-team-scoped role — gates the VEX import button. */
  projectRole?: TeamScopedRole;
  /**
   * Historical (read-only) snapshot mode (feature #28). When `true`, the VEX
   * import control is disabled (importing into an old snapshot would mutate the
   * current findings). The export + PDF report controls stay enabled — they are
   * read-only.
   */
  readOnly?: boolean;
  /**
   * W9 #52 — current severity selection (mirrors the parent tab's `severity`
   * state). When the user mounts the severity facet via "+ Add filter", the
   * toolbar renders an inline MultiSelect bound to this prop.
   */
  severity: VulnSeverity[];
  onSeverityChange: (value: VulnSeverity[]) => void;
  /**
   * W9 #52 — current license-category selection (mirrors the parent tab's
   * `licenseCategory` state). Same mount-on-demand pattern as `severity`.
   */
  licenseCategory: LicenseCategoryName[];
  onLicenseCategoryChange: (value: LicenseCategoryName[]) => void;
  /**
   * W9 #52 — facets the user has opted into via "+ Add filter". Mounting is
   * independent of having a non-empty selection so a user can dismount an
   * empty facet without re-mounting later.
   */
  mountedExtraFilters: Set<VulnerabilitiesExtraFilter>;
  onMountExtraFilter: (filter: VulnerabilitiesExtraFilter) => void;
  onUnmountExtraFilter: (filter: VulnerabilitiesExtraFilter) => void;
  /**
   * W9 #52 — column-picker catalog + visibility set. Parents own the state so
   * row-render code can decide which cells to draw. The picker delegates the
   * localStorage round-trip via `storageKey`.
   */
  columnsCatalog: ColumnsPickerColumn[];
  visibleColumns: Set<string>;
  onVisibleColumnsChange: (next: Set<string>) => void;
  columnsStorageKey: string;
  className?: string;
}

/**
 * W9 #52 — `VulnSeverity` options exposed by the optional severity facet.
 * Mirrors the parent's `VALID_SEVERITY` tuple so the inline MultiSelect lists
 * the same tokens that already round-trip through the URL.
 */
const VULN_SEVERITY_OPTIONS: VulnSeverity[] = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
  "unknown",
];

/**
 * W9 #52 — license-category options for the optional license facet. Mirrors
 * Components/Vulnerabilities `VALID_LICENSE`.
 */
const LICENSE_CATEGORY_OPTIONS: LicenseCategoryName[] = [
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
];

export function VulnerabilitiesToolbar({
  groupBy,
  onGroupByChange,
  search,
  onSearchChange,
  sort,
  onSortKeyChange,
  status,
  onStatusChange,
  minEpss,
  onMinEpssChange,
  reachable,
  onReachableChange,
  vexSuppressedOnly,
  onVexSuppressedOnlyChange,
  projectId,
  projectName,
  projectRole = "developer",
  readOnly = false,
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
}: VulnerabilitiesToolbarProps) {
  const { t } = useTranslation("project_detail");

  // W9 #52 — the "+ Add filter" dropdown surfaces facets that are not
  // already inline. Any facet with a non-empty selection counts as "active"
  // for the dropdown's check indicator even if the user has not explicitly
  // mounted its MultiSelect — so a chart deep-link populated value shows as
  // active and the user understands the filter is on.
  const availableExtraFilters: MoreFiltersMenuOption[] = [
    {
      id: "severity",
      label: t("extra_filters.severity_label"),
    },
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
      // Toggle: if the facet is already mounted *and* empty, drop it; else
      // mount it (idempotent if already mounted).
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

  /**
   * Parse a free-text EPSS threshold. Empty → null (filter off). Numbers are
   * clamped to [0, 1] so a typo can't send an out-of-range value to the wire.
   */
  function handleMinEpssInput(raw: string) {
    const trimmed = raw.trim();
    if (trimmed.length === 0) {
      onMinEpssChange(null);
      return;
    }
    const parsed = Number.parseFloat(trimmed);
    if (!Number.isFinite(parsed)) return;
    const clamped = Math.min(1, Math.max(0, parsed));
    onMinEpssChange(clamped);
  }
  return (
    <div
      className={cn(
        "flex flex-col gap-3 border-b bg-background px-4 py-3 lg:flex-row lg:items-end lg:gap-4",
        className,
      )}
      data-testid="vulnerabilities-toolbar"
    >
      {/* W9-#53 — group-by segmented control. Mirrors the hand-rolled control
          on ComponentsToolbar (role=group + aria-pressed buttons). Always
          visible; picking "By upgrade" hides the flat-list-only controls. */}
      <div className="flex flex-col">
        <span className="text-xs font-medium text-muted-foreground">
          {t("vulnerabilities.group_by.label")}
        </span>
        <div
          role="group"
          aria-label={t("vulnerabilities.group_by.label")}
          data-testid="vulnerabilities-group-by"
          className="mt-1 inline-flex h-9 items-stretch overflow-hidden rounded-md border border-input bg-background"
        >
          {GROUP_BY_OPTIONS.map((opt, idx) => {
            const isActive = groupBy === opt.value;
            return (
              <button
                key={opt.key}
                type="button"
                aria-pressed={isActive}
                data-testid={`vulnerabilities-group-by-${opt.key}`}
                data-active={isActive ? "true" : "false"}
                onClick={() => onGroupByChange(opt.value)}
                className={cn(
                  "px-3 text-xs font-medium transition-colors duration-fast ease-out-soft",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                  idx > 0 && "border-l border-input",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                {t(`vulnerabilities.group_by.${opt.key}`)}
              </button>
            );
          })}
        </div>
      </div>

      {groupBy === "flat" ? (
        <>
      <div className="flex-1">
        <label
          htmlFor="vulnerabilities-search"
          className="block text-xs font-medium text-muted-foreground"
        >
          {t("vulnerabilities.toolbar.search_label")}
        </label>
        <Input
          id="vulnerabilities-search"
          type="search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder={t("vulnerabilities.toolbar.search_placeholder")}
          data-testid="vulnerabilities-search"
          className="mt-1 h-9"
        />
      </div>

      {/* KEV feature — sort select. Column sorts stay clickable on the
          headers; this select exists because the default composite
          "priority" ranking (KEV → severity → EPSS) is not a column. */}
      <div className="flex flex-col">
        <label
          htmlFor="vulnerabilities-sort-select"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("vulnerabilities.toolbar.sort_label")}
        </label>
        <select
          id="vulnerabilities-sort-select"
          value={sort}
          onChange={(event) =>
            onSortKeyChange(event.target.value as VulnerabilitySortKey)
          }
          className="mt-1 h-9 w-56 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="vulnerabilities-sort-select"
        >
          {SORT_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(SORT_OPTION_LABEL_KEY[opt])}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="vulnerabilities-status-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("vulnerabilities.toolbar.status_label")}
        </label>
        <MultiSelect
          id="vulnerabilities-status-filter"
          testId="vulnerabilities-status-filter"
          className="w-44"
          label={t("vulnerabilities.toolbar.status_label")}
          options={STATUS_OPTIONS.map((opt) => ({
            value: opt,
            label: t(`vulnerabilities.status.${opt}`),
          }))}
          selected={status}
          onChange={(next) => onStatusChange(next as VulnFindingStatus[])}
        />
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="vulnerabilities-reachable-filter"
          className="text-xs font-medium text-muted-foreground"
          title={t("vulnerabilities.reachability.tooltip_filter")}
        >
          {t("vulnerabilities.toolbar.reachable_label")}
        </label>
        <select
          id="vulnerabilities-reachable-filter"
          value={reachable ?? ""}
          onChange={(event) =>
            onReachableChange(
              event.target.value === ""
                ? null
                : (event.target.value as ReachabilityFilter),
            )
          }
          className="mt-1 h-9 w-40 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="vulnerabilities-reachable-filter"
        >
          <option value="">
            {t("vulnerabilities.toolbar.reachable_any")}
          </option>
          {REACHABLE_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(`vulnerabilities.toolbar.reachable_option.${opt}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="vulnerabilities-min-epss"
          className="text-xs font-medium text-muted-foreground"
          title={t("vulnerabilities.epss.tooltip", {
            defaultValue:
              "EPSS — probability this CVE is exploited in the wild within 30 days. Complements CVSS (severity).",
          })}
        >
          {t("vulnerabilities.filter.minEpss", {
            defaultValue: "EPSS ≥",
          })}
        </label>
        <div className="mt-1 flex items-center gap-1">
          <Input
            id="vulnerabilities-min-epss"
            type="number"
            inputMode="decimal"
            min={0}
            max={1}
            step={0.01}
            value={minEpss ?? ""}
            onChange={(event) => handleMinEpssInput(event.target.value)}
            placeholder={t("vulnerabilities.filter.minEpssPlaceholder", {
              defaultValue: "0.00–1.00",
            })}
            data-testid="vulnerabilities-min-epss"
            className="h-9 w-24 font-mono tabular-nums"
            aria-describedby="vulnerabilities-min-epss-hint"
          />
          {minEpss != null ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-9 px-2 text-xs"
              onClick={() => onMinEpssChange(null)}
              data-testid="vulnerabilities-min-epss-clear"
            >
              {t("vulnerabilities.filter.minEpssClear", {
                defaultValue: "Clear",
              })}
            </Button>
          ) : null}
        </div>
        <span id="vulnerabilities-min-epss-hint" className="sr-only">
          {t("vulnerabilities.filter.minEpssHint", {
            defaultValue:
              "Enter an EPSS probability between 0 and 1 to keep findings at or above it.",
          })}
        </span>
      </div>

      <div className="flex flex-col">
        <span className="text-xs font-medium text-muted-foreground">
          {t("vulnerabilities.vex.filter_label")}
        </span>
        <label className="mt-1 flex h-9 items-center gap-2 rounded-md border border-input bg-background px-2 text-sm">
          <input
            type="checkbox"
            checked={vexSuppressedOnly}
            onChange={(event) =>
              onVexSuppressedOnlyChange(event.target.checked)
            }
            data-testid="vulnerabilities-vex-suppressed-filter"
            className="h-4 w-4 rounded border-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <span>{t("vulnerabilities.vex.filter_suppressed")}</span>
        </label>
      </div>

      {/* W9 #52 — optional severity facet, mounted on demand via the
          "+ Add filter" dropdown. The MultiSelect shape mirrors the Status
          control above so a triager swaps between them without re-learning
          the affordance. */}
      {showSeverityFacet ? (
        <div
          className="flex flex-col"
          data-testid="vulnerabilities-severity-facet"
        >
          <div className="flex items-center gap-1">
            <label
              htmlFor="vulnerabilities-severity-filter"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("extra_filters.severity_label")}
            </label>
            <button
              type="button"
              data-testid="vulnerabilities-severity-facet-remove"
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
            id="vulnerabilities-severity-filter"
            testId="vulnerabilities-severity-filter"
            className="w-44"
            label={t("extra_filters.severity_label")}
            options={VULN_SEVERITY_OPTIONS.map((opt) => ({
              value: opt,
              label: t(`severity.${opt}`),
            }))}
            selected={severity}
            onChange={(next) => onSeverityChange(next as VulnSeverity[])}
          />
        </div>
      ) : null}

      {/* W9 #52 — optional license category facet (W2 #33 was chip-only via
          chart deep-link; the dropdown surfaces a real MultiSelect now). */}
      {showLicenseFacet ? (
        <div
          className="flex flex-col"
          data-testid="vulnerabilities-license-category-facet"
        >
          <div className="flex items-center gap-1">
            <label
              htmlFor="vulnerabilities-license-category-filter"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("extra_filters.license_category_label")}
            </label>
            <button
              type="button"
              data-testid="vulnerabilities-license-category-facet-remove"
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
            id="vulnerabilities-license-category-filter"
            testId="vulnerabilities-license-category-filter"
            className="w-44"
            label={t("extra_filters.license_category_label")}
            options={LICENSE_CATEGORY_OPTIONS.map((opt) => ({
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
          testId="vulnerabilities-more-filters-trigger"
          disabled={readOnly}
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
          testId="vulnerabilities-columns-picker-trigger"
        />
      </div>

      <VexExportMenu
        projectId={projectId}
        projectName={projectName}
      />

      <VexImportDialog
        projectId={projectId}
        projectRole={projectRole}
        readOnly={readOnly}
      />
      {/* PDF report trigger lives on the Reports tab now — the
          ``vuln-pdf`` generate card there owns the download. The toolbar
          prop API still threads ``vulnReport`` so the existing hook +
          tests stay intact; only the button moved. */}
        </>
      ) : null}
    </div>
  );
}
