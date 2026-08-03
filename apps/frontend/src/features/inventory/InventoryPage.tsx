// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { Boxes } from "lucide-react";
import {
  forwardRef,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
} from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { Virtuoso } from "react-virtuoso";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { InventoryDrawer } from "@/features/inventory/components/InventoryDrawer";
import { InventoryRow } from "@/features/inventory/components/InventoryRow";
import { InventoryToolbar } from "@/features/inventory/components/InventoryToolbar";
import { useInventoryComponents } from "@/features/inventory/api/useInventory";
import type {
  InventorySortKey,
  SortOrder,
} from "@/features/inventory/api/inventoryApi";
import type {
  ComponentSeverity,
  LicenseCategoryName,
} from "@/features/projects/api/projectDetailApi";
import { ProblemError } from "@/lib/problem";

/**
 * InventoryPage — organization-wide component inventory (S2).
 *
 * The question this page exists for is "where is this package, and what would
 * a fix have to touch". Every row is one package across every project the
 * viewer can reach, so the two columns that matter most are the spread
 * (projects, versions) rather than any single project's detail.
 *
 * "In use" is the backend's definition: present in a project's latest
 * SUCCEEDED scan. A project whose newest attempt failed still contributes its
 * last good scan, and a package dropped in the newest scan disappears here.
 *
 * Filter state lives in the URL so a filtered view is reload-safe and
 * shareable, matching the project detail tabs. Params are prefixed `inv_` so
 * they cannot collide with anything else that lands on this route.
 */

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
const VALID_SORT = new Set<InventorySortKey>([
  "name",
  "project_count",
  "severity",
  "license",
]);

function parseList<T extends string>(raw: string | null, valid: Set<T>): T[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((value) => value.trim())
    .filter((value): value is T => valid.has(value as T));
}

function parseFreeList(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((value) => value.trim())
    .filter((value) => value.length > 0);
}

function parseTriState(raw: string | null): boolean | undefined {
  if (raw === "true") return true;
  if (raw === "false") return false;
  return undefined;
}

function parseSort(raw: string | null): InventorySortKey {
  return raw && VALID_SORT.has(raw as InventorySortKey)
    ? (raw as InventorySortKey)
    : "project_count";
}

/**
 * Virtuoso puts plain `div`s between the table and its rows; they flatten
 * away, except the scroller, which carries `tabindex` and so needs a role the
 * table grammar allows. Same treatment as the Components tab.
 */
const VIRTUOSO_TABLE_BODY = {
  Scroller: forwardRef<HTMLDivElement, ComponentPropsWithoutRef<"div">>(
    function VirtuosoScroller(props, ref) {
      return <div ref={ref} {...props} role="rowgroup" />;
    },
  ),
};

export function InventoryPage() {
  const { t } = useTranslation("inventory");
  const [searchParams, setSearchParams] = useSearchParams();

  const [search, setSearch] = useState(
    () => searchParams.get("inv_search") ?? "",
  );
  const [debouncedSearch, setDebouncedSearch] = useState(search);
  const [packageType, setPackageType] = useState<string[]>(() =>
    parseFreeList(searchParams.get("inv_package_type")),
  );
  const [severity, setSeverity] = useState<ComponentSeverity[]>(() =>
    parseList<ComponentSeverity>(searchParams.get("inv_severity"), VALID_SEVERITY),
  );
  const [licenseCategory, setLicenseCategory] = useState<LicenseCategoryName[]>(
    () =>
      parseList<LicenseCategoryName>(
        searchParams.get("inv_license_category"),
        VALID_LICENSE,
      ),
  );
  const [eol, setEol] = useState<boolean | undefined>(() =>
    parseTriState(searchParams.get("inv_eol")),
  );
  const [outdated, setOutdated] = useState<boolean | undefined>(() =>
    parseTriState(searchParams.get("inv_outdated")),
  );
  const [sort, setSort] = useState<InventorySortKey>(() =>
    parseSort(searchParams.get("inv_sort")),
  );
  const [order, setOrder] = useState<SortOrder>(() =>
    searchParams.get("inv_order") === "asc" ? "asc" : "desc",
  );

  const drawerComponentId = searchParams.get("inv_component");

  const setDrawerComponent = useCallback(
    (componentId: string | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (componentId) next.set("inv_component", componentId);
          else next.delete("inv_component");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedSearch(search), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [search]);

  // Mirror filter state into the URL. Defaults are omitted so a clean view has
  // a clean URL and the back button steps through real changes only.
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (debouncedSearch) next.set("inv_search", debouncedSearch);
        else next.delete("inv_search");
        if (packageType.length)
          next.set("inv_package_type", packageType.join(","));
        else next.delete("inv_package_type");
        if (severity.length) next.set("inv_severity", severity.join(","));
        else next.delete("inv_severity");
        if (licenseCategory.length)
          next.set("inv_license_category", licenseCategory.join(","));
        else next.delete("inv_license_category");
        if (eol === true || eol === false) next.set("inv_eol", String(eol));
        else next.delete("inv_eol");
        if (outdated === true || outdated === false)
          next.set("inv_outdated", String(outdated));
        else next.delete("inv_outdated");
        if (sort !== "project_count") next.set("inv_sort", sort);
        else next.delete("inv_sort");
        if (order !== "desc") next.set("inv_order", order);
        else next.delete("inv_order");
        return next;
      },
      { replace: true },
    );
  }, [
    debouncedSearch,
    packageType,
    severity,
    licenseCategory,
    eol,
    outdated,
    sort,
    order,
    setSearchParams,
  ]);

  const filters = useMemo(
    () => ({
      q: debouncedSearch,
      packageType,
      severity,
      licenseCategory,
      eol,
      outdated,
      sort,
      order,
    }),
    [
      debouncedSearch,
      packageType,
      severity,
      licenseCategory,
      eol,
      outdated,
      sort,
      order,
    ],
  );

  const query = useInventoryComponents(filters);
  const pages = query.data?.pages ?? [];
  const items = pages.flatMap((page) => page.items);
  const total = pages[0]?.total ?? 0;

  const isEmpty = !query.isLoading && !query.isError && items.length === 0;

  return (
    <div className="flex min-h-screen flex-col" data-testid="inventory-page">
      <PageHeader variant="bar" title={t("page.title")} />

      <InventoryToolbar
        search={search}
        onSearchChange={setSearch}
        packageType={packageType}
        onPackageTypeChange={setPackageType}
        severity={severity}
        onSeverityChange={setSeverity}
        licenseCategory={licenseCategory}
        onLicenseCategoryChange={setLicenseCategory}
        eol={eol}
        onEolChange={setEol}
        outdated={outdated}
        onOutdatedChange={setOutdated}
        sort={sort}
        onSortChange={setSort}
        order={order}
        onOrderChange={setOrder}
      />

      {/* Counts, not a bare list — "how much of the portfolio is this" is the
          reason someone opened the page. `data-*` mirrors let the E2E harness
          assert without reading formatted copy. */}
      <div
        className="border-b px-6 py-3 text-sm text-muted-foreground"
        data-testid="inventory-summary"
        data-total={total}
        data-loaded={items.length}
      >
        {t("summary.count", { total, loaded: items.length })}
      </div>

      <main className="flex flex-1 flex-col">
        {query.isError ? (
          <div className="px-6 py-6">
            <Alert variant="destructive" data-testid="inventory-error">
              <AlertDescription>
                {query.error instanceof ProblemError
                  ? query.error.detail
                  : t("errors.load_failed")}
              </AlertDescription>
            </Alert>
          </div>
        ) : null}

        {query.isLoading ? (
          <div
            className="flex flex-col gap-2 px-6 py-4"
            data-testid="inventory-loading"
          >
            {Array.from({ length: 6 }).map((_, index) => (
              <Skeleton key={index} className="h-10 w-full" />
            ))}
          </div>
        ) : null}

        {isEmpty ? (
          <EmptyState
            data-testid="inventory-empty"
            className="m-6"
            icon={<Boxes />}
            title={t("empty.title")}
            /* A term that found nothing here is the one moment where the
               difference between this page and the search page is actionable:
               this lists what each project's latest scan found, the search page
               reaches back through the whole scan history. Without a term
               there is nothing to carry across, so the generic copy stands. */
            description={
              debouncedSearch ? t("empty.wider.hint") : t("empty.subtitle")
            }
            action={
              debouncedSearch ? (
                <Button asChild variant="outline" size="sm">
                  <Link
                    to={`/search?kind=components&q=${encodeURIComponent(debouncedSearch)}`}
                    data-testid="inventory-empty-search-history"
                  >
                    {t("empty.wider.action")}
                  </Link>
                </Button>
              ) : undefined
            }
          />
        ) : null}

        {!query.isLoading && !query.isError && items.length > 0 ? (
          <div className="overflow-x-auto px-6">
            <div
              className="min-w-[960px]"
              role="table"
              aria-rowcount={total + 1}
              data-testid="inventory-table"
            >
              <div
                role="row"
                className="flex items-center gap-3 border-b py-2 text-xs font-medium text-muted-foreground"
              >
                <span role="columnheader" className="flex-1">
                  {t("col.name")}
                </span>
                <span role="columnheader" className="w-24">
                  {t("col.type")}
                </span>
                <span role="columnheader" className="w-28">
                  {t("col.projects")}
                </span>
                <span role="columnheader" className="w-40">
                  {t("col.versions")}
                </span>
                <span role="columnheader" className="w-24">
                  {t("col.severity")}
                </span>
                <span role="columnheader" className="w-20">
                  {t("col.cves")}
                </span>
                <span role="columnheader" className="w-32">
                  {t("col.license")}
                </span>
              </div>
              <Virtuoso
                components={VIRTUOSO_TABLE_BODY}
                data={items}
                style={{
                  height: "calc(100vh - var(--layout-header) - 260px)",
                }}
                endReached={() => {
                  if (query.hasNextPage && !query.isFetchingNextPage) {
                    void query.fetchNextPage();
                  }
                }}
                itemContent={(index, row) => (
                  <InventoryRow
                    row={row}
                    rowIndex={index}
                    onOpen={() => setDrawerComponent(row.component_id)}
                  />
                )}
              />
            </div>
          </div>
        ) : null}

        {query.isFetchingNextPage ? (
          <div className="px-6 py-2" data-testid="inventory-loading-more">
            <Skeleton className="h-8 w-full" />
          </div>
        ) : null}
      </main>

      <InventoryDrawer
        componentId={drawerComponentId}
        onClose={() => setDrawerComponent(null)}
      />
    </div>
  );
}
