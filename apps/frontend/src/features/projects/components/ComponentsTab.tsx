import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Virtuoso } from "react-virtuoso";

import { Alert, AlertDescription } from "@/components/ui/alert";
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
import type {
  ComponentSeverity,
  ComponentSortKey,
  ComponentSummary,
  DependencyScopeFilter,
  LicenseCategoryName,
  SortOrder,
} from "@/features/projects/api/projectDetailApi";
import { useComponents } from "@/features/projects/api/useComponents";
import { ActiveFilterChips } from "@/features/projects/components/ActiveFilterChips";
import { ComponentDrawer } from "@/features/projects/components/ComponentDrawer";
import { ComponentsToolbar } from "@/features/projects/components/ComponentsToolbar";
import { DependencyScopeBadge } from "@/features/projects/components/DependencyScopeBadge";
import { AxisPill } from "@/features/projects/components/AxisPill";
import { DependencyTypeBadge } from "@/features/projects/components/DependencyTypeBadge";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { LicenseDistributionChart } from "@/features/projects/components/LicenseDistributionChart";
import { SeverityDistributionChart } from "@/features/projects/components/SeverityDistributionChart";
import { useProjectOverview } from "@/features/projects/api/useProjectOverview";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";
import { ProblemError } from "@/lib/problem";
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
  const [sort, setSort] = useState<ComponentSortKey>(() =>
    parseSort(searchParams.get("sort")),
  );
  const [order, setOrder] = useState<SortOrder>(() =>
    parseOrder(searchParams.get("order")),
  );

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

  return (
    <div data-testid="components-tab" className="flex flex-1 flex-col">
      {/* Two summary cards mirroring the Overview tab. Clicking a segment or
          legend row narrows the list below to that bucket only (single-select
          replace). The shared overview query backs them so they always show
          the *full* project distribution — the row count below is what
          reflects the active filter. */}
      {severityDistribution || licenseDistribution ? (
        <div
          className="grid items-start gap-4 border-b p-4 md:grid-cols-2"
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
                    setSeverity([key]);
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
                    setLicenseCategory([key]);
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
      />

      <ActiveFilterChips
        severity={severity}
        onSeverityChange={setSeverity}
        licenseCategory={licenseCategory}
        onLicenseCategoryChange={setLicenseCategory}
      />

      <div
        className="flex items-center justify-between border-b px-4 py-2 text-xs text-muted-foreground"
        data-testid="components-summary"
        data-total={total}
        data-loaded={items.length}
      >
        <span>
          {t("components.summary", { loaded: items.length, total })}
        </span>
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
    </div>
  );
}

interface ComponentsTableHeaderProps {
  currentSort: SortState | null;
  onSortChange: (next: SortState | null) => void;
}

function ComponentsTableHeader({
  currentSort,
  onSortChange,
}: ComponentsTableHeaderProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className="flex items-center gap-3 border-b bg-muted/30 px-4 text-xs font-medium uppercase tracking-wide text-muted-foreground"
      style={{ height: "32px" }}
      data-testid="components-header"
    >
      {/* W4-B #17 — Name / Severity / License are sortable; click cycles
          unset → asc → desc → unset. URL `?sort=` / `?order=` mirror the
          state below (existing effect). The remaining columns are static.
          Width follow-up (2026-05-27): LICENSE is now SPDX-only at w-28 and a
          separate POLICY column (w-24) holds the Allowed/Forbidden badge —
          stacking the two inside one cell wrapped to two lines on the user's
          ~700-800px main pane. The Name cell carries `min-w-[180px]` so it
          can no longer collapse to zero. */}
      <span className="flex-1 min-w-[180px]">
        <SortableColumnHeader
          column="name"
          label={t("components.col.name")}
          currentSort={currentSort}
          onSort={onSortChange}
          testId="components-sort-header-name"
        />
      </span>
      <span className="w-24">{t("components.col.type")}</span>
      <span className="w-24 text-right">{t("components.col.version")}</span>
      <span className="w-28">
        <SortableColumnHeader
          column="license"
          label={t("components.col.license")}
          currentSort={currentSort}
          onSort={onSortChange}
          testId="components-sort-header-license"
        />
      </span>
      <span className="w-24">{t("components.col.policy")}</span>
      <span className="w-24">{t("components.col.usage")}</span>
      <span className="w-24">
        <SortableColumnHeader
          column="severity"
          label={t("components.col.severity")}
          currentSort={currentSort}
          onSort={onSortChange}
          testId="components-sort-header-severity"
        />
      </span>
      <span className="w-12 text-right">{t("components.col.vulns")}</span>
    </div>
  );
}


interface ComponentRowProps {
  component: ComponentSummary;
  rowIndex: number;
  onSelect: () => void;
}

function ComponentRow({ component, rowIndex, onSelect }: ComponentRowProps) {
  const { t } = useTranslation("project_detail");
  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid="component-row"
      data-component-id={component.id}
      data-row-index={rowIndex}
      className={cn(
        "flex w-full items-center gap-3 border-b px-4 text-left text-sm hover:bg-muted/50",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
      )}
      style={{ height: "var(--table-row)" }}
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
      <span className="w-24">
        <DependencyTypeBadge
          direct={component.direct}
          depth={component.depth}
        />
      </span>
      <span
        className="w-24 truncate text-right font-mono text-xs"
        title={component.version}
      >
        {component.version}
      </span>
      {/* LICENSE is the SPDX identifier on its own — the policy category lives
          in the next cell so the row stays single-line. `language-mono` styling
          keeps SPDX expressions (`(MIT OR Apache-2.0)`) readable. */}
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
      <span className="w-24">
        <LicenseCategoryBadge category={component.license_category} />
      </span>
      <span className="w-24">
        <DependencyScopeBadge scope={component.dependency_scope} />
      </span>
      <span className="w-24">
        <SeverityBadge severity={component.severity_max} />
      </span>
      <span
        className="w-12 text-right font-mono text-xs tabular-nums"
        data-testid="component-row-vuln-count"
      >
        {component.vulnerability_count}
      </span>
    </button>
  );
}
