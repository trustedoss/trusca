import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Virtuoso } from "react-virtuoso";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  ComponentSeverity,
  ComponentSortKey,
  ComponentSummary,
  LicenseCategoryName,
  SortOrder,
} from "@/features/projects/api/projectDetailApi";
import { useComponents } from "@/features/projects/api/useComponents";
import { ComponentDrawer } from "@/features/projects/components/ComponentDrawer";
import { ComponentsToolbar } from "@/features/projects/components/ComponentsToolbar";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
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

export interface ComponentsTabProps {
  projectId: string;
}

export function ComponentsTab({ projectId }: ComponentsTabProps) {
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
        if (sort !== "name") next.set("sort", sort);
        else next.delete("sort");
        if (order !== "asc") next.set("order", order);
        else next.delete("order");
        return next;
      },
      { replace: true },
    );
  }, [debouncedSearch, severity, licenseCategory, sort, order, setSearchParams]);

  const filters = useMemo(
    () => ({
      search: debouncedSearch,
      severity,
      license_category: licenseCategory,
      sort,
      order,
      pageSize: PAGE_SIZE,
    }),
    [debouncedSearch, severity, licenseCategory, sort, order],
  );

  const components = useComponents(projectId, filters);

  const items: ComponentSummary[] = useMemo(() => {
    if (!components.data) return [];
    return components.data.pages.flatMap((page) => page.items);
  }, [components.data]);

  const total = components.data?.pages[0]?.total ?? 0;

  return (
    <div data-testid="components-tab" className="flex flex-1 flex-col">
      <ComponentsToolbar
        search={search}
        onSearchChange={setSearch}
        severity={severity}
        onSeverityChange={setSeverity}
        licenseCategory={licenseCategory}
        onLicenseCategoryChange={setLicenseCategory}
        sort={sort}
        onSortChange={setSort}
        order={order}
        onOrderChange={setOrder}
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
        <>
          <ComponentsTableHeader />
          <div
            className="flex-1"
            data-testid="components-virtual"
            data-total={total}
            data-loaded={items.length}
          >
            <Virtuoso
              data={items}
              endReached={() => {
                if (components.hasNextPage && !components.isFetchingNextPage) {
                  void components.fetchNextPage();
                }
              }}
              style={{ height: "calc(100vh - var(--layout-header) - 240px)" }}
              itemContent={(index, item) => (
                <ComponentRow
                  component={item}
                  rowIndex={index}
                  onSelect={() => setDrawerComponent(item.id)}
                />
              )}
            />
          </div>
        </>
      ) : null}

      <ComponentDrawer
        open={drawerOpen}
        componentId={drawerId}
        onOpenChange={(open) => {
          if (!open) setDrawerComponent(null);
        }}
      />
    </div>
  );
}

function ComponentsTableHeader() {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className="flex items-center gap-3 border-b bg-muted/30 px-4 text-xs font-medium uppercase tracking-wide text-muted-foreground"
      style={{ height: "32px" }}
      data-testid="components-header"
    >
      <span className="flex-1">{t("components.col.name")}</span>
      <span className="w-32 text-right">{t("components.col.version")}</span>
      <span className="w-40">{t("components.col.license")}</span>
      <span className="w-32">{t("components.col.severity")}</span>
      <span className="w-16 text-right">{t("components.col.vulns")}</span>
    </div>
  );
}

interface ComponentRowProps {
  component: ComponentSummary;
  rowIndex: number;
  onSelect: () => void;
}

function ComponentRow({ component, rowIndex, onSelect }: ComponentRowProps) {
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
      <span className="flex flex-1 items-center gap-2 truncate">
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
      <span
        className="w-32 truncate text-right font-mono text-xs"
        title={component.version}
      >
        {component.version}
      </span>
      <span className="w-40">
        <LicenseCategoryBadge category={component.license_category} />
      </span>
      <span className="w-32">
        <SeverityBadge severity={component.severity_max} />
      </span>
      <span
        className="w-16 text-right font-mono text-xs tabular-nums"
        data-testid="component-row-vuln-count"
      >
        {component.vulnerability_count}
      </span>
    </button>
  );
}
