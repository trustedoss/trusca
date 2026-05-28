import { FileCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Virtuoso } from "react-virtuoso";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";
import type {
  LicenseFindingKind,
  LicenseListItem,
  LicenseSortKey,
  SortOrder,
} from "@/features/projects/api/licensesApi";
import { useLicenses } from "@/features/projects/api/useLicenses";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { AxisPill } from "@/features/projects/components/AxisPill";
import { LicenseDistributionChart } from "@/features/projects/components/LicenseDistributionChart";
import { LicenseDrawer } from "@/features/projects/components/LicenseDrawer";
import { LicenseKindBadge } from "@/features/projects/components/LicenseKindBadge";
import { LicensesToolbar } from "@/features/projects/components/LicensesToolbar";
import { ProblemError } from "@/lib/problem";
import { toggleSingleValue } from "@/lib/searchParamsToggle";
import { cn } from "@/lib/utils";

/**
 * LicensesTab — Phase 3 PR #12.
 *
 * Virtualized license findings table + distribution chart + drawer for the
 * project detail page. Mirrors the structure of `VulnerabilitiesTab`:
 *
 *   - `useLicenses` is a paginated `useQuery` keyed on the entire filter
 *     tuple. Filter or sort changes naturally invalidate the cached page
 *     and refetch from offset 0.
 *   - Search input is debounced 300ms before it hits the query.
 *   - Filters, sort, pagination, and the selected drawer finding id are
 *     mirrored into URL search params (deep-link + reload survival). The
 *     drawer key is `?license=<finding_id>` so it doesn't collide with
 *     ComponentsTab's `?drawer=<component_id>` (PR #10) or
 *     VulnerabilitiesTab's `?vuln=<finding_id>` (PR #11).
 *   - Virtuoso renders fixed 40px rows (CLAUDE.md compact density).
 *
 * The list response carries no `created_at` column for the row, so the
 * "Discovered" column is intentionally omitted (the brief permits this).
 *
 * Read-only domain: no analyst workflow, no transitions, no audit log.
 */

const PAGE_SIZE = 100;

const VALID_CATEGORY = new Set<LicenseCategoryName>([
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
]);

const VALID_KIND = new Set<LicenseFindingKind>([
  "declared",
  "concluded",
  "detected",
]);

const VALID_SORT = new Set<LicenseSortKey>([
  "category",
  "name",
  "spdx_id",
  "affected_count",
]);

function parseList<T extends string>(raw: string | null, valid: Set<T>): T[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((v) => v.trim())
    .filter((v): v is T => valid.has(v as T));
}

function parseSort(raw: string | null): LicenseSortKey {
  if (raw && VALID_SORT.has(raw as LicenseSortKey)) {
    return raw as LicenseSortKey;
  }
  return "category";
}

function parseOrder(raw: string | null): SortOrder {
  return raw === "asc" ? "asc" : "desc";
}

function parsePage(raw: string | null): number {
  const n = raw ? Number.parseInt(raw, 10) : 1;
  if (!Number.isFinite(n) || n < 1) return 1;
  return n;
}

export interface LicensesTabProps {
  projectId: string;
  /**
   * Pinned snapshot scan id (feature #28). When set, the list reflects that
   * historical scan instead of the latest succeeded one. Omit → latest.
   */
  scanId?: string;
}

export function LicensesTab({ projectId, scanId }: LicensesTabProps) {
  const { t } = useTranslation("project_detail");
  const [searchParams, setSearchParams] = useSearchParams();

  // ----- filter state, hydrated from URL on first render -------------------
  const [search, setSearch] = useState(() => searchParams.get("search") ?? "");
  const [debouncedSearch, setDebouncedSearch] = useState(search);
  const [categories, setCategories] = useState<LicenseCategoryName[]>(() =>
    parseList<LicenseCategoryName>(
      searchParams.get("license_category"),
      VALID_CATEGORY,
    ),
  );
  const [kinds, setKinds] = useState<LicenseFindingKind[]>(() =>
    parseList<LicenseFindingKind>(searchParams.get("kind"), VALID_KIND),
  );
  const [sort, setSort] = useState<LicenseSortKey>(() =>
    parseSort(searchParams.get("sort")),
  );
  const [order, setOrder] = useState<SortOrder>(() =>
    parseOrder(searchParams.get("order")),
  );
  const [page, setPage] = useState<number>(() =>
    parsePage(searchParams.get("page")),
  );

  // Drawer state — `?license=<finding_id>` so reload restores the selection.
  const drawerId = searchParams.get("license");
  const drawerOpen = drawerId != null && drawerId.length > 0;

  function setDrawerLicense(findingId: string | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (findingId) {
          next.set("license", findingId);
        } else {
          next.delete("license");
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
    debounceRef.current = setTimeout(() => {
      setDebouncedSearch(search);
      // A new search resets pagination to page 1 — otherwise the user could
      // be stuck on page 5 of a now-tiny result set.
      setPage(1);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [search]);

  // Mirror filter state into URL params for deep-linking + reload-survival.
  // We omit defaults so canonical URLs stay short.
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (debouncedSearch) next.set("search", debouncedSearch);
        else next.delete("search");
        if (categories.length)
          next.set("license_category", categories.join(","));
        else next.delete("license_category");
        if (kinds.length) next.set("kind", kinds.join(","));
        else next.delete("kind");
        if (sort !== "category") next.set("sort", sort);
        else next.delete("sort");
        if (order !== "desc") next.set("order", order);
        else next.delete("order");
        if (page !== 1) next.set("page", String(page));
        else next.delete("page");
        return next;
      },
      { replace: true },
    );
  }, [
    debouncedSearch,
    categories,
    kinds,
    sort,
    order,
    page,
    setSearchParams,
  ]);

  const filters = useMemo(
    () => ({
      search: debouncedSearch,
      categories,
      kinds,
      sort,
      order,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
      scanId,
    }),
    [debouncedSearch, categories, kinds, sort, order, page, scanId],
  );

  const licenses = useLicenses(projectId, filters);

  const items: LicenseListItem[] = licenses.data?.items ?? [];
  const total = licenses.data?.total ?? 0;
  const distribution = licenses.data?.distribution;

  return (
    <div data-testid="licenses-tab" className="flex flex-1 flex-col">
      {distribution ? (
        <div className="border-b px-4 py-3" data-testid="licenses-distribution">
          <div className="mb-2 flex items-center gap-2 text-xs">
            <AxisPill>{t("overview.license_card.axis_components")}</AxisPill>
          </div>
          <LicenseDistributionChart
            distribution={distribution}
            onSegmentClick={(key) => {
              // W9-#57 (2026-05-28) — re-clicking the only-active category
              // toggles the filter OFF. The historical "same key = no-op"
              // rule forced users to hunt down the chip-clear control; the
              // toggle now matches the natural mental model.
              setCategories((prev) => toggleSingleValue(prev, key));
              setPage(1);
            }}
          />
        </div>
      ) : null}

      <LicensesToolbar
        search={search}
        onSearchChange={setSearch}
        categories={categories}
        onCategoriesChange={(next) => {
          setCategories(next);
          setPage(1);
        }}
        kinds={kinds}
        onKindsChange={(next) => {
          setKinds(next);
          setPage(1);
        }}
        sort={sort}
        onSortChange={(next) => {
          setSort(next);
          setPage(1);
        }}
        order={order}
        onOrderChange={(next) => {
          setOrder(next);
          setPage(1);
        }}
      />

      <div
        className="flex items-center justify-between border-b px-4 py-2 text-xs text-muted-foreground"
        data-testid="licenses-summary"
        data-total={total}
        data-loaded={items.length}
      >
        <span>
          {t("licenses.summary", {
            loaded: items.length,
            total,
          })}
        </span>
      </div>

      {licenses.isError ? (
        <div className="px-6 py-6">
          <Alert variant="destructive" data-testid="licenses-error">
            <AlertDescription>
              {licenses.error instanceof ProblemError
                ? licenses.error.detail
                : t("licenses.errors.load_list")}
            </AlertDescription>
          </Alert>
        </div>
      ) : null}

      {licenses.isLoading ? (
        <div
          className="flex flex-col gap-2 px-4 py-3"
          data-testid="licenses-loading"
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : null}

      {!licenses.isLoading && !licenses.isError && items.length === 0 ? (
        <EmptyState
          data-testid="licenses-empty"
          className="m-6"
          icon={<FileCheck />}
          title={t("licenses.empty.title")}
          description={t("licenses.empty.description")}
        />
      ) : null}

      {!licenses.isLoading && !licenses.isError && items.length > 0 ? (
        <>
          <LicensesTableHeader />
          <div
            className="flex-1"
            data-testid="licenses-virtual"
            data-total={total}
            data-loaded={items.length}
          >
            <Virtuoso
              data={items}
              style={{
                height: "calc(100vh - var(--layout-header) - 320px)",
              }}
              itemContent={(index, item) => (
                <LicenseRow
                  license={item}
                  rowIndex={index}
                  onSelect={() => setDrawerLicense(item.id)}
                />
              )}
            />
          </div>
        </>
      ) : null}

      <LicenseDrawer
        open={drawerOpen}
        findingId={drawerId}
        onOpenChange={(open) => {
          if (!open) setDrawerLicense(null);
        }}
      />
    </div>
  );
}

function LicensesTableHeader() {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className="flex items-center gap-3 border-b bg-muted/30 px-4 text-xs font-medium uppercase tracking-wide text-muted-foreground"
      style={{ height: "32px" }}
      data-testid="licenses-header"
    >
      <span className="w-44">{t("licenses.column.spdx_id")}</span>
      <span className="flex-1">{t("licenses.column.name")}</span>
      <span className="w-32">{t("licenses.column.category")}</span>
      <span className="w-28">{t("licenses.column.kind")}</span>
      <span className="w-20 text-right">
        {t("licenses.column.affected_count")}
      </span>
    </div>
  );
}

interface LicenseRowProps {
  license: LicenseListItem;
  rowIndex: number;
  onSelect: () => void;
}

function LicenseRow({ license, rowIndex, onSelect }: LicenseRowProps) {
  const { t } = useTranslation("project_detail");
  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid="license-row"
      data-finding-id={license.id}
      data-spdx-id={license.spdx_id ?? ""}
      data-category={license.category}
      data-kind={license.kind}
      data-row-index={rowIndex}
      className={cn(
        "flex w-full items-center gap-3 border-b px-4 text-left text-sm transition-colors duration-fast ease-out-soft hover:bg-muted/50",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
      )}
      style={{ height: "var(--table-row)" }}
    >
      <span
        className="w-44 truncate font-mono text-xs"
        title={license.spdx_id ?? license.name}
      >
        {license.spdx_id ?? t("licenses.row.no_spdx_id")}
      </span>
      <span className="flex-1 truncate" title={license.name}>
        {license.name}
      </span>
      <span className="w-32">
        <LicenseCategoryBadge category={license.category} />
      </span>
      <span className="w-28">
        <LicenseKindBadge kind={license.kind} />
      </span>
      <span
        className="w-20 text-right font-mono text-xs tabular-nums"
        data-testid="license-row-affected-count"
      >
        {license.affected_count}
      </span>
    </button>
  );
}
