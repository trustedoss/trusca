import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { Virtuoso } from "react-virtuoso";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type SortState,
  SortableColumnHeader,
} from "@/components/ui/sortable-column-header";
import {
  type ColumnsPickerColumn,
  loadInitialVisibility,
} from "@/components/filters/ColumnsPicker";
import type {
  ComponentSeverity,
  ComponentSortKey,
  ComponentSummary,
  DependencyScopeFilter,
  LicenseCategoryName,
  SortOrder,
} from "@/features/projects/api/projectDetailApi";
import { useComponents } from "@/features/projects/api/useComponents";
import { useScanScopeFilter } from "@/features/projects/api/useScanScopeFilter";
import { ActiveFilterChips } from "@/features/projects/components/ActiveFilterChips";
import { ComponentDrawer } from "@/features/projects/components/ComponentDrawer";
import { DependencyGraph } from "@/features/projects/components/DependencyGraph";
import {
  ComponentsToolbar,
  type ComponentsExtraFilter,
} from "@/features/projects/components/ComponentsToolbar";
import { DependencyScopeBadge } from "@/features/projects/components/DependencyScopeBadge";
import { EolBadge } from "@/features/projects/components/EolBadge";
import { CurrencyBadge } from "@/features/projects/components/CurrencyBadge";
import { AxisPill } from "@/features/projects/components/AxisPill";
import { DependencyTypeBadge } from "@/features/projects/components/DependencyTypeBadge";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { LicenseDistributionChart } from "@/features/projects/components/LicenseDistributionChart";
import { SeverityDistributionChart } from "@/features/projects/components/SeverityDistributionChart";
import { useProjectOverview } from "@/features/projects/api/useProjectOverview";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";
import { ProblemError } from "@/lib/problem";
import { toggleSingleValue } from "@/lib/searchParamsToggle";
import { cn } from "@/lib/utils";

/**
 * ComponentsTab — Phase 3 PR #10.
 *
 * Virtualized component table + drawer for the project detail page.
 *
 *   - `useComponents` is an infinite-cursor query keyed on the entire filter
 *     tuple. A filter or sort change naturally invalidates the cache and
 *     refetches from offset 0.
 *   - Search input is debounced 300ms before it hits the query.
 *   - Filters and sort are mirrored into URL search params so deep-links
 *     and reload preserve state. The selected drawer component id is
 *     mirrored too (`?drawer=<componentId>`) per CLAUDE.md "Routing".
 *   - Virtuoso renders a fixed 40px row (CLAUDE.md compact density). On
 *     `endReached` we call `fetchNextPage()` for true infinite scroll.
 */

const PAGE_SIZE = 100;

/**
 * W9 #52 — column-picker catalog for the Components table. `name` and
 * `version` identify the row and are always present; the other columns are
 * user-toggleable. Persisted under `COMPONENT_COLUMNS_STORAGE_KEY` so per-tab
 * preferences survive reload independently of the Vulnerabilities tab.
 */
const COMPONENT_COLUMNS_STORAGE_KEY = "column-visibility:components";

function getComponentColumnsCatalog(
  t: (key: string) => string,
): ColumnsPickerColumn[] {
  return [
    { id: "name", label: t("components.col.name"), required: true },
    { id: "type", label: t("components.col.type") },
    { id: "version", label: t("components.col.version"), required: true },
    { id: "license", label: t("components.col.license") },
    { id: "policy", label: t("components.col.policy") },
    { id: "usage", label: t("components.col.usage") },
    { id: "eol", label: t("components.col.eol") },
    { id: "currency", label: t("components.col.currency") },
    { id: "severity", label: t("components.col.severity") },
    { id: "vulns", label: t("components.col.vulns") },
  ];
}

const VALID_SEVERITY = new Set<ComponentSeverity>([
  "critical",
  "high",
  "medium",
  "low",
  "info",
  "none",
]);

const VALID_LICENSE = new Set<LicenseCategoryName>([
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
]);

const VALID_SCOPE = new Set<DependencyScopeFilter>([
  "required",
  "optional",
  "unspecified",
]);

const VALID_SORT = new Set<ComponentSortKey>(["name", "severity", "license"]);

function parseList<T extends string>(
  raw: string | null,
  valid: Set<T>,
): T[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((v) => v.trim())
    .filter((v): v is T => valid.has(v as T));
}

function parseSort(raw: string | null): ComponentSortKey {
  if (raw && VALID_SORT.has(raw as ComponentSortKey)) {
    return raw as ComponentSortKey;
  }
  return "name";
}

function parseOrder(raw: string | null): SortOrder {
  return raw === "desc" ? "desc" : "asc";
}

/**
 * W2 #31 — hydrate the dependency-type 3-state from a URL string. Anything
 * other than the two literals collapses to `null` (= "All") so a typoed URL
 * never sticks the toolbar in an unreachable state.
 */
function parseDirect(raw: string | null): boolean | null {
  if (raw === "true") return true;
  if (raw === "false") return false;
  return null;
}

export interface ComponentsTabProps {
  projectId: string;
  /**
   * Pinned snapshot scan id (feature #28). When set, the list reflects that
   * historical scan instead of the latest succeeded one. Omit → latest.
   */
  scanId?: string;
}

export function ComponentsTab({ projectId, scanId }: ComponentsTabProps) {
  const { t } = useTranslation("project_detail");
  const [searchParams, setSearchParams] = useSearchParams();

  // ----- filter state, hydrated from URL on first render -------------------
  const [search, setSearch] = useState(() => searchParams.get("search") ?? "");
  const [debouncedSearch, setDebouncedSearch] = useState(search);
  const [severity, setSeverity] = useState<ComponentSeverity[]>(() =>
    parseList<ComponentSeverity>(searchParams.get("severity"), VALID_SEVERITY),
  );
  const [licenseCategory, setLicenseCategory] = useState<LicenseCategoryName[]>(
    () =>
      parseList<LicenseCategoryName>(
        searchParams.get("license_category"),
        VALID_LICENSE,
      ),
  );
  // W2 #31 — Direct/Transitive 3-state and BD-style "Usage" multi-select.
  // Both hydrate from URL so deep-links/reload keep the facet selection.
  const [direct, setDirect] = useState<boolean | null>(() =>
    parseDirect(searchParams.get("direct")),
  );
  const [dependencyScope, setDependencyScope] = useState<
    DependencyScopeFilter[]
  >(() =>
    parseList<DependencyScopeFilter>(
      searchParams.get("dependency_scope"),
      VALID_SCOPE,
    ),
  );
  // Phase M — EOL-only toggle. Boolean facet (`?eol=true` when on); the
  // "off" state means "no opinion" (both buckets), never `?eol=false`.
  const [eolOnly, setEolOnly] = useState<boolean>(
    () => searchParams.get("eol") === "true",
  );
  // Version-currency-only toggle (sibling of eolOnly). `?outdated=true` when
  // on; "off" means "no opinion" (both buckets), never `?outdated=false`.
  const [outdatedOnly, setOutdatedOnly] = useState<boolean>(
    () => searchParams.get("outdated") === "true",
  );
  const [sort, setSort] = useState<ComponentSortKey>(() =>
    parseSort(searchParams.get("sort")),
  );
  const [order, setOrder] = useState<SortOrder>(() =>
    parseOrder(searchParams.get("order")),
  );

  // W9 #52 — "+ Add filter" mount-on-demand facets. Same shape as the
  // Vulnerabilities tab: seed from URL state so a deep-link auto-mounts.
  const [mountedExtraFilters, setMountedExtraFilters] = useState<
    Set<ComponentsExtraFilter>
  >(() => {
    const next = new Set<ComponentsExtraFilter>();
    const sevParam = searchParams.get("severity");
    if (sevParam && sevParam.length > 0) next.add("severity");
    const licParam = searchParams.get("license_category");
    if (licParam && licParam.length > 0) next.add("license_category");
    return next;
  });
  const mountExtraFilter = (filter: ComponentsExtraFilter) => {
    setMountedExtraFilters((prev) => {
      if (prev.has(filter)) return prev;
      const next = new Set(prev);
      next.add(filter);
      return next;
    });
  };
  const unmountExtraFilter = (filter: ComponentsExtraFilter) => {
    setMountedExtraFilters((prev) => {
      if (!prev.has(filter)) return prev;
      const next = new Set(prev);
      next.delete(filter);
      return next;
    });
  };

  // W9 #52 — column visibility, hydrated from per-tab localStorage.
  const columnsCatalog = useMemo(
    () => getComponentColumnsCatalog((k) => t(k)),
    [t],
  );
  const [visibleColumns, setVisibleColumns] = useState<Set<string>>(() =>
    loadInitialVisibility(COMPONENT_COLUMNS_STORAGE_KEY, columnsCatalog),
  );

  // View toggle — `?view=graph` swaps the virtual table for the dependency
  // graph (BomLens parity H-1). Anything other than the literal "graph"
  // collapses to the default table so a typoed URL never sticks.
  const view: "table" | "graph" =
    searchParams.get("view") === "graph" ? "graph" : "table";

  function setView(next: "table" | "graph") {
    setSearchParams(
      (prev) => {
        const nextParams = new URLSearchParams(prev);
        if (next === "graph") nextParams.set("view", "graph");
        else nextParams.delete("view");
        return nextParams;
      },
      { replace: true },
    );
  }

  // Drawer state — `?drawer=<componentId>` so reload restores the selection.
  const drawerId = searchParams.get("drawer");
  const drawerOpen = drawerId != null && drawerId.length > 0;

  function setDrawerComponent(componentId: string | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (componentId) {
          next.set("drawer", componentId);
        } else {
          next.delete("drawer");
        }
        return next;
      },
      { replace: true },
    );
  }

  // Debounce the search input → 300ms before a network call.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedSearch(search), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [search]);

  // Mirror filter state into URL params for deep-linking + reload-survival.
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (debouncedSearch) next.set("search", debouncedSearch);
        else next.delete("search");
        if (severity.length) next.set("severity", severity.join(","));
        else next.delete("severity");
        if (licenseCategory.length)
          next.set("license_category", licenseCategory.join(","));
        else next.delete("license_category");
        // W2 #31 — `?direct=true|false`; drop the key entirely on "All" so
        // the URL stays clean for the default state.
        if (direct === true) next.set("direct", "true");
        else if (direct === false) next.set("direct", "false");
        else next.delete("direct");
        // W2 #31 — `?dependency_scope=required,optional,unspecified`. Comma
        // join matches the parseList convention used by severity / license.
        if (dependencyScope.length)
          next.set("dependency_scope", dependencyScope.join(","));
        else next.delete("dependency_scope");
        // Phase M — `?eol=true` only when the toggle is on.
        if (eolOnly) next.set("eol", "true");
        else next.delete("eol");
        // Version currency — `?outdated=true` only when the toggle is on.
        if (outdatedOnly) next.set("outdated", "true");
        else next.delete("outdated");
        if (sort !== "name") next.set("sort", sort);
        else next.delete("sort");
        if (order !== "asc") next.set("order", order);
        else next.delete("order");
        return next;
      },
      { replace: true },
    );
  }, [
    debouncedSearch,
    severity,
    licenseCategory,
    direct,
    dependencyScope,
    eolOnly,
    outdatedOnly,
    sort,
    order,
    setSearchParams,
  ]);

  const filters = useMemo(
    () => ({
      search: debouncedSearch,
      severity,
      license_category: licenseCategory,
      direct,
      dependency_scope: dependencyScope,
      eol: eolOnly ? true : null,
      outdated: outdatedOnly ? true : null,
      sort,
      order,
      pageSize: PAGE_SIZE,
      scanId,
    }),
    [
      debouncedSearch,
      severity,
      licenseCategory,
      direct,
      dependencyScope,
      eolOnly,
      outdatedOnly,
      sort,
      order,
      scanId,
    ],
  );

  const components = useComponents(projectId, filters);

  const items: ComponentSummary[] = useMemo(() => {
    if (!components.data) return [];
    return components.data.pages.flatMap((page) => page.items);
  }, [components.data]);

  const total = components.data?.pages[0]?.total ?? 0;

  // W4-B #17 — sortable-column-header callback. Cycle is unset→asc→desc→unset;
  // we mirror the next state into the existing `sort` / `order` state which
  // already flows into URL params via the effect below.
  const currentSort: SortState | null = useMemo(() => {
    // Treat the default "name asc" as the un-sorted bucket so a click on the
    // Name header cycles through the same asc/desc/unsorted states the user
    // sees on the other columns — otherwise the column would never have an
    // "unsorted" state and the cycle would be stuck on asc→desc→asc.
    if (sort === "name" && order === "asc") return null;
    return { key: sort, order };
  }, [sort, order]);

  function handleSortChange(next: SortState | null) {
    if (!next) {
      setSort("name");
      setOrder("asc");
      return;
    }
    setSort(next.key as ComponentSortKey);
    setOrder(next.order);
  }

  // Distribution data for the two summary cards above the toolbar. Same
  // overview query the Overview tab + VulnerabilitiesTab use, so TanStack
  // Query dedupes the request via the shared query key.
  const overview = useProjectOverview(projectId, scanId);
  const severityDistribution = overview.data?.severity_distribution;
  const licenseDistribution = overview.data?.license_distribution;

  // Phase K — runtime-scope filter transparency. Non-null only when the
  // rendered scan's worker telemetry says components were actually dropped.
  const scopeFilter = useScanScopeFilter(scanId, overview.data?.recent_scans);

  return (
    <div data-testid="components-tab" className="flex flex-1 flex-col">
      {/* View toggle — Table (virtual list) vs Graph (dependency graph, H-1).
          The choice is mirrored into `?view=graph` so reload / deep-link keep
          it. Rendered as a segmented control, keyboard-reachable, with the
          active view marked via aria-pressed (not color alone). */}
      <div
        className="flex items-center justify-end border-b border-border/60 px-6 py-2"
        data-testid="components-view-toggle"
        role="group"
        aria-label={t("components.view_toggle.aria")}
      >
        <div className="inline-flex overflow-hidden rounded-md border">
          <Button
            type="button"
            variant={view === "table" ? "secondary" : "ghost"}
            size="sm"
            className="h-7 rounded-none px-3"
            aria-pressed={view === "table"}
            data-testid="components-view-toggle-table"
            data-active={view === "table"}
            onClick={() => setView("table")}
          >
            {t("components.view_toggle.table")}
          </Button>
          <Button
            type="button"
            variant={view === "graph" ? "secondary" : "ghost"}
            size="sm"
            className="h-7 rounded-none px-3"
            aria-pressed={view === "graph"}
            data-testid="components-view-toggle-graph"
            data-active={view === "graph"}
            onClick={() => setView("graph")}
          >
            {t("components.view_toggle.graph")}
          </Button>
        </div>
      </div>

      {view === "graph" ? (
        <div className="flex flex-1 flex-col" data-testid="components-graph-view">
          <DependencyGraph projectId={projectId} scanId={scanId} />
        </div>
      ) : (
        <>
      {/* Two summary cards mirroring the Overview tab. Clicking a segment or
          legend row narrows the list below to that bucket only (single-select
          replace). The shared overview query backs them so they always show
          the *full* project distribution — the row count below is what
          reflects the active filter. */}
      {severityDistribution || licenseDistribution ? (
        <div
          // W11-C polish — distribution band aligns to the px-6 + py-4 gutter
          // shared by toolbar / rows below (Vercel deployments-1 axis).
          // Border-bottom softened to /60 so the seam reads as part of the
          // table stack rather than a heavy divider.
          className="grid items-start gap-4 border-b border-border/60 px-6 py-4 md:grid-cols-2"
          data-testid="components-distribution-cards"
        >
          {severityDistribution ? (
            <Card data-testid="components-severity-card">
              <CardHeader>
                <CardTitle className="flex items-baseline gap-2 text-base">
                  <span>{t("overview.severity_card.title")}</span>
                  <AxisPill>
                    {t("overview.severity_card.axis_components")}
                  </AxisPill>
                </CardTitle>
                <CardDescription>
                  {t("overview.severity_card.subtitle")}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <SeverityDistributionChart
                  distribution={severityDistribution}
                  onSegmentClick={(key) => {
                    // W9-#57 — same-segment re-click toggles the filter off.
                    setSeverity((prev) => toggleSingleValue(prev, key));
                  }}
                />
              </CardContent>
            </Card>
          ) : null}
          {licenseDistribution ? (
            <Card data-testid="components-license-card">
              <CardHeader>
                <CardTitle className="flex items-baseline gap-2 text-base">
                  <span>{t("overview.license_card.title")}</span>
                  <AxisPill>
                    {t("overview.license_card.axis_components")}
                  </AxisPill>
                </CardTitle>
                <CardDescription>
                  {t("overview.license_card.subtitle")}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <LicenseDistributionChart
                  distribution={licenseDistribution}
                  onSegmentClick={(key) => {
                    // W9-#57 — same-segment re-click toggles the filter off.
                    setLicenseCategory((prev) => toggleSingleValue(prev, key));
                  }}
                />
              </CardContent>
            </Card>
          ) : null}
        </div>
      ) : null}

      <ComponentsToolbar
        search={search}
        onSearchChange={setSearch}
        direct={direct}
        onDirectChange={setDirect}
        dependencyScope={dependencyScope}
        onDependencyScopeChange={setDependencyScope}
        eolOnly={eolOnly}
        onEolOnlyChange={setEolOnly}
        outdatedOnly={outdatedOnly}
        onOutdatedOnlyChange={setOutdatedOnly}
        severity={severity}
        onSeverityChange={setSeverity}
        licenseCategory={licenseCategory}
        onLicenseCategoryChange={setLicenseCategory}
        mountedExtraFilters={mountedExtraFilters}
        onMountExtraFilter={mountExtraFilter}
        onUnmountExtraFilter={unmountExtraFilter}
        columnsCatalog={columnsCatalog}
        visibleColumns={visibleColumns}
        onVisibleColumnsChange={setVisibleColumns}
        columnsStorageKey={COMPONENT_COLUMNS_STORAGE_KEY}
      />

      <ActiveFilterChips
        severity={severity}
        onSeverityChange={setSeverity}
        licenseCategory={licenseCategory}
        onLicenseCategoryChange={setLicenseCategory}
      />

      <div
        // W11-C polish — summary band aligns to px-6 gutter and uses
        // border-border/60 so the seam stays light at 40 px density.
        className="flex items-center justify-between border-b border-border/60 px-6 py-2 text-xs text-muted-foreground"
        data-testid="components-summary"
        data-total={total}
        data-loaded={items.length}
      >
        <span>
          {t("components.summary", { loaded: items.length, total })}
        </span>
        {scopeFilter ? (
          <span
            data-testid="components-scope-filter-note"
            data-dropped={scopeFilter.totalDropped}
            title={Object.entries(scopeFilter.dropped)
              .map(([ecosystem, count]) => `${ecosystem}: ${count}`)
              .join(", ")}
          >
            {t("components.summary_scope_filtered", {
              count: scopeFilter.totalDropped,
            })}
          </span>
        ) : null}
      </div>

      {components.isError ? (
        <div className="px-6 py-6">
          <Alert variant="destructive" data-testid="components-error">
            <AlertDescription>
              {components.error instanceof ProblemError
                ? components.error.detail
                : t("components.errors.load_failed")}
            </AlertDescription>
          </Alert>
        </div>
      ) : null}

      {components.isLoading ? (
        <div
          className="flex flex-col gap-2 px-4 py-3"
          data-testid="components-loading"
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : null}

      {!components.isLoading && !components.isError && items.length === 0 ? (
        <Card className="m-6" data-testid="components-empty">
          <CardHeader>
            <CardTitle className="text-base">
              {t("components.empty.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {t("components.empty.subtitle")}
          </CardContent>
        </Card>
      ) : null}

      {!components.isLoading && !components.isError && items.length > 0 ? (
        // The row + header together need ~820px to render the eight columns
        // without squeezing the Name cell (flex-1) to zero — narrower
        // viewports were rendering the TYPE badge *inside* the COMPONENT
        // header because the Name cell had no min width. We keep the table
        // at its natural width and let the outer wrapper scroll horizontally
        // instead of column-hiding (the user explicitly wants all eight
        // columns visible at once).
        <div className="flex flex-1 flex-col overflow-x-auto">
          <div className="min-w-[820px] flex flex-1 flex-col">
            <ComponentsTableHeader
              currentSort={currentSort}
              onSortChange={handleSortChange}
              visibleColumns={visibleColumns}
            />
            <div
              className="flex-1"
              data-testid="components-virtual"
              data-total={total}
              data-loaded={items.length}
            >
              <Virtuoso
                data={items}
                endReached={() => {
                  if (
                    components.hasNextPage &&
                    !components.isFetchingNextPage
                  ) {
                    void components.fetchNextPage();
                  }
                }}
                style={{
                  height: "calc(100vh - var(--layout-header) - 240px)",
                }}
                itemContent={(index, item) => (
                  <ComponentRow
                    component={item}
                    rowIndex={index}
                    projectId={projectId}
                    visibleColumns={visibleColumns}
                    onSelect={() => setDrawerComponent(item.id)}
                  />
                )}
              />
            </div>
          </div>
        </div>
      ) : null}

      <ComponentDrawer
        open={drawerOpen}
        componentId={drawerId}
        projectId={projectId}
        onOpenChange={(open) => {
          if (!open) setDrawerComponent(null);
        }}
      />
        </>
      )}
    </div>
  );
}

interface ComponentsTableHeaderProps {
  currentSort: SortState | null;
  onSortChange: (next: SortState | null) => void;
  /**
   * W9 #52 — column ids the user has chosen to show. `name` and `version`
   * are required (the ColumnsPicker disables their checkboxes); other
   * columns are gated on this set.
   */
  visibleColumns: Set<string>;
}

function ComponentsTableHeader({
  currentSort,
  onSortChange,
  visibleColumns,
}: ComponentsTableHeaderProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      // W11-C polish — table header lands on the canonical px-6 gutter +
      // --muted token (no /30 opacity hack), softer border-border/60 seam,
      // and tracking-wider matches the SortableColumnHeader chip typography.
      // Height stays 32 px (compact identity).
      className="flex items-center gap-3 border-b border-border/60 bg-muted px-6 text-xs font-medium uppercase tracking-wider text-muted-foreground"
      style={{ height: "32px" }}
      data-testid="components-header"
    >
      {/* W4-B #17 — Name / Severity / License are sortable; click cycles
          unset → asc → desc → unset. URL `?sort=` / `?order=` mirror the
          state below (existing effect). The remaining columns are static.
          W9 #52 — optional cells are gated on `visibleColumns`; required
          cells (`name`, `version`) always render. */}
      <span className="flex-1 min-w-[180px]">
        <SortableColumnHeader
          column="name"
          label={t("components.col.name")}
          currentSort={currentSort}
          onSort={onSortChange}
          testId="components-sort-header-name"
        />
      </span>
      {visibleColumns.has("type") ? (
        <span className="w-24" data-testid="components-header-cell-type">
          {t("components.col.type")}
        </span>
      ) : null}
      <span className="w-24 text-right">{t("components.col.version")}</span>
      {visibleColumns.has("license") ? (
        <span className="w-28" data-testid="components-header-cell-license">
          <SortableColumnHeader
            column="license"
            label={t("components.col.license")}
            currentSort={currentSort}
            onSort={onSortChange}
            testId="components-sort-header-license"
          />
        </span>
      ) : null}
      {visibleColumns.has("policy") ? (
        <span className="w-24" data-testid="components-header-cell-policy">
          {t("components.col.policy")}
        </span>
      ) : null}
      {visibleColumns.has("usage") ? (
        <span className="w-24" data-testid="components-header-cell-usage">
          {t("components.col.usage")}
        </span>
      ) : null}
      {visibleColumns.has("eol") ? (
        <span className="w-16" data-testid="components-header-cell-eol">
          {t("components.col.eol")}
        </span>
      ) : null}
      {visibleColumns.has("currency") ? (
        <span className="w-20" data-testid="components-header-cell-currency">
          {t("components.col.currency")}
        </span>
      ) : null}
      {visibleColumns.has("severity") ? (
        <span className="w-24" data-testid="components-header-cell-severity">
          <SortableColumnHeader
            column="severity"
            label={t("components.col.severity")}
            currentSort={currentSort}
            onSort={onSortChange}
            testId="components-sort-header-severity"
          />
        </span>
      ) : null}
      {visibleColumns.has("vulns") ? (
        <span
          className="w-12 text-right"
          data-testid="components-header-cell-vulns"
        >
          {t("components.col.vulns")}
        </span>
      ) : null}
    </div>
  );
}


interface ComponentRowProps {
  component: ComponentSummary;
  rowIndex: number;
  /** M-22 — needed to build the CVEs-count deep-link into the Vulns tab. */
  projectId: string;
  /** W9 #52 — column ids the row should render. */
  visibleColumns: Set<string>;
  onSelect: () => void;
}

function ComponentRow({
  component,
  rowIndex,
  projectId,
  visibleColumns,
  onSelect,
}: ComponentRowProps) {
  const { t } = useTranslation("project_detail");
  // M-22 — the row used to be a single <button>, but the CVEs-count cell is
  // now a <Link> and nesting an anchor inside a button is invalid DOM. The
  // structure mirrors VulnerabilityRow (checkbox precedent): a non-interactive
  // row container, one inner "open" button covering the read-only cells, and
  // the count link as a sibling so both stay independently keyboard-reachable.
  return (
    <div
      data-testid="component-row"
      data-component-id={component.id}
      data-row-index={rowIndex}
      className={cn(
        // W11-C polish — Vercel deployments-1 row tone: bg-card surface,
        // accent hover via Linear motion tokens, softer /60 border, px-6
        // gutter aligned to the header / toolbar / distribution cards.
        // 40 px row height stays unchanged (compact identity).
        "flex w-full items-center gap-3 border-b border-border/60 bg-card px-6 text-left text-sm",
        "transition-colors duration-fast ease-out-soft hover:bg-accent",
      )}
      style={{ height: "var(--table-row)" }}
    >
      <button
        type="button"
        onClick={onSelect}
        data-testid="component-row-open"
        className={cn(
          "flex h-full flex-1 items-center gap-3 text-left",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
        )}
      >
      <span className="flex flex-1 min-w-[180px] items-center gap-2 truncate">
        <span className="truncate font-medium" title={component.name}>
          {component.name}
        </span>
        {component.purl ? (
          <span
            className="truncate font-mono text-xs text-muted-foreground"
            title={component.purl}
          >
            {component.purl}
          </span>
        ) : null}
      </span>
      {visibleColumns.has("type") ? (
        <span className="w-24" data-testid="component-row-cell-type">
          <DependencyTypeBadge
            direct={component.direct}
            depth={component.depth}
          />
        </span>
      ) : null}
      <span
        className="w-24 truncate text-right font-mono text-xs"
        title={component.version}
      >
        {component.version}
      </span>
      {/* LICENSE is the SPDX identifier on its own — the policy category lives
          in the next cell so the row stays single-line. `language-mono` styling
          keeps SPDX expressions (`(MIT OR Apache-2.0)`) readable.
          W9 #52 — optional cells (license, policy, usage, severity, vulns)
          are gated on `visibleColumns`. */}
      {visibleColumns.has("license") ? (
        <span
          className={cn(
            "w-28 truncate font-mono text-xs",
            component.license ? "text-foreground" : "text-muted-foreground",
          )}
          data-testid="component-row-license-spdx"
          data-license-spdx={component.license ?? ""}
          title={component.license ?? t("components.license.unknown_dash")}
        >
          {component.license ?? t("components.license.unknown_dash")}
        </span>
      ) : null}
      {visibleColumns.has("policy") ? (
        <span className="w-24" data-testid="component-row-cell-policy">
          <LicenseCategoryBadge category={component.license_category} />
        </span>
      ) : null}
      {visibleColumns.has("usage") ? (
        <span className="w-24" data-testid="component-row-cell-usage">
          <DependencyScopeBadge scope={component.dependency_scope} />
        </span>
      ) : null}
      {visibleColumns.has("eol") ? (
        <span className="w-16" data-testid="component-row-cell-eol">
          <EolBadge
            eolState={component.eol_state}
            eolDate={component.eol_date}
          />
        </span>
      ) : null}
      {visibleColumns.has("currency") ? (
        <span className="w-20" data-testid="component-row-cell-currency">
          <CurrencyBadge
            currencyState={component.currency_state}
            currencyLatest={component.currency_latest}
          />
        </span>
      ) : null}
      {visibleColumns.has("severity") ? (
        <span className="w-24" data-testid="component-row-cell-severity">
          <SeverityBadge severity={component.severity_max} />
        </span>
      ) : null}
      </button>
      {/* M-22 — the CVEs count deep-links into the Vulnerabilities tab
          pre-filtered on this component's name (backend search matches
          component names). It sits OUTSIDE the open-button so the anchor is
          valid DOM; stopPropagation keeps a click from also bubbling into
          any ancestor handlers. Zero counts stay plain text — there is
          nothing to filter to. */}
      {visibleColumns.has("vulns") ? (
        <span
          className="w-12 text-right font-mono text-xs tabular-nums"
          data-testid="component-row-vuln-count"
        >
          {component.vulnerability_count > 0 ? (
            <Link
              to={`/projects/${projectId}?tab=vulnerabilities&search=${encodeURIComponent(component.name)}`}
              onClick={(e) => e.stopPropagation()}
              data-testid="component-row-vuln-link"
              aria-label={t("components.vulns_link_aria", {
                count: component.vulnerability_count,
                name: component.name,
              })}
              className="underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {component.vulnerability_count}
            </Link>
          ) : (
            component.vulnerability_count
          )}
        </span>
      ) : null}
    </div>
  );
}
