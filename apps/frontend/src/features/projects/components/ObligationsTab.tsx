// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { CheckCircle2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Virtuoso } from "react-virtuoso";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { VIRTUOSO_TABLE_BODY } from "@/components/ui/virtuoso-table-body";
import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";
import {
  KNOWN_OBLIGATION_KINDS,
  type ObligationListItem,
  type ObligationSortKey,
  type SortOrder,
} from "@/features/projects/api/obligationsApi";
import { useNotice } from "@/features/projects/api/useNotice";
import { useObligations } from "@/features/projects/api/useObligations";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { ObligationDrawer } from "@/features/projects/components/ObligationDrawer";
import { ObligationsToolbar } from "@/features/projects/components/ObligationsToolbar";
import {
  OBLIGATIONS_SEARCH_PARAM,
  readTabSearchParam,
  writeTabSearchParam,
} from "@/features/projects/components/tabSearchParam";
import { problemMessage } from "@/lib/problemMessage";
import { cn } from "@/lib/utils";

/**
 * ObligationsTab — Phase 3 PR #13.
 *
 * Virtualized obligations table + per-kind summary + NOTICE download +
 * drawer for the project detail page. Read-only domain, URL search-param
 * state, debounced search, drawer key
 * (`?obligation=<id>`) chosen to not collide with `?drawer=<cv_id>`,
 * `?vuln=<id>`, `?license=<id>`.
 *
 * The kind axis is open: KNOWN_OBLIGATION_KINDS is the canonical surface for
 * filter chips, but raw catalog rows may carry kinds outside this list. The
 * row + drawer render unknown kinds verbatim.
 */

const PAGE_SIZE = 100;

const VALID_CATEGORY = new Set<LicenseCategoryName>([
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
]);

const VALID_SORT = new Set<ObligationSortKey>([
  "category",
  "license_name",
  "kind",
  "affected_count",
]);

const KNOWN_KINDS_SET = new Set<string>(KNOWN_OBLIGATION_KINDS);

function parseList<T extends string>(raw: string | null, valid: Set<T>): T[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((v) => v.trim())
    .filter((v): v is T => valid.has(v as T));
}

function parseKindList(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((v) => v.trim())
    .filter((v) => v.length > 0 && KNOWN_KINDS_SET.has(v));
}

function parseSort(raw: string | null): ObligationSortKey {
  if (raw && VALID_SORT.has(raw as ObligationSortKey)) {
    return raw as ObligationSortKey;
  }
  return "category";
}

function parseOrder(raw: string | null): SortOrder {
  return raw === "asc" ? "asc" : "desc";
}

export interface ObligationsTabProps {
  projectId: string;
  projectName?: string | null;
  /**
   * Pinned snapshot scan id (feature #28). When set, the list reflects that
   * historical scan instead of the latest succeeded one. Omit → latest.
   */
  scanId?: string;
}

export function ObligationsTab({
  projectId,
  projectName,
  scanId,
}: ObligationsTabProps) {
  const { t } = useTranslation("project_detail");
  const [searchParams, setSearchParams] = useSearchParams();

  const [search, setSearch] = useState(() =>
    readTabSearchParam(searchParams, OBLIGATIONS_SEARCH_PARAM),
  );
  const [debouncedSearch, setDebouncedSearch] = useState(search);
  const [kinds, setKinds] = useState<string[]>(() =>
    parseKindList(searchParams.get("kind")),
  );
  const [categories, setCategories] = useState<LicenseCategoryName[]>(() =>
    parseList<LicenseCategoryName>(
      searchParams.get("license_category"),
      VALID_CATEGORY,
    ),
  );
  const [sort, setSort] = useState<ObligationSortKey>(() =>
    parseSort(searchParams.get("sort")),
  );
  const [order, setOrder] = useState<SortOrder>(() =>
    parseOrder(searchParams.get("order")),
  );
  const drawerId = searchParams.get("obligation");
  const drawerOpen = drawerId != null && drawerId.length > 0;

  /**
   * Pushed, not replaced, so Back closes the drawer rather than leaving the
   * tab and discarding the filters the user set to find this row.
   */
  function setDrawerObligation(obligationId: string | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (obligationId) {
          next.set("obligation", obligationId);
        } else {
          next.delete("obligation");
        }
        return next;
      },
      { replace: false },
    );
  }

  // Debounce search → 300ms.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedSearch(search);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [search]);

  // Mirror filter state into URL params for deep-linking + reload-survival.
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        writeTabSearchParam(next, OBLIGATIONS_SEARCH_PARAM, debouncedSearch);
        if (kinds.length) next.set("kind", kinds.join(","));
        else next.delete("kind");
        if (categories.length)
          next.set("license_category", categories.join(","));
        else next.delete("license_category");
        if (sort !== "category") next.set("sort", sort);
        else next.delete("sort");
        if (order !== "desc") next.set("order", order);
        else next.delete("order");
        // Infinite now: the parameter it used to write pointed at a page the
        // UI offered no way to turn to.
        next.delete("page");
        return next;
      },
      { replace: true },
    );
  }, [debouncedSearch, kinds, categories, sort, order, setSearchParams]);

  const filters = useMemo(
    () => ({
      search: debouncedSearch,
      kinds,
      categories,
      sort,
      order,
      limit: PAGE_SIZE,
      scanId,
    }),
    [debouncedSearch, kinds, categories, sort, order, scanId],
  );

  const obligations = useObligations(projectId, filters);
  const notice = useNotice(projectId, projectName ?? undefined, { scanId });

  /** Whether anything is narrowing the list, which decides the empty state. */
  const hasNarrowingFilters =
    debouncedSearch.trim().length > 0 ||
    kinds.length > 0 ||
    categories.length > 0;

  function clearAllFilters() {
    setSearch("");
    setDebouncedSearch("");
    setKinds([]);
    setCategories([]);
  }

  const pages = obligations.data?.pages;
  const items: ObligationListItem[] = useMemo(
    () => (pages ?? []).flatMap((p) => p.items),
    [pages],
  );
  // Both are whole-result facts the server repeats on every page, so the
  // first page answers for all of them.
  const total = pages?.[0]?.total ?? 0;
  const distribution = pages?.[0]?.distribution ?? {};

  return (
    <div data-testid="obligations-tab" className="flex flex-1 flex-col">
      <DistributionStrip distribution={distribution} />

      <ObligationsToolbar
        search={search}
        onSearchChange={setSearch}
        kinds={kinds}
        onKindsChange={(next) => {
          setKinds(next);
        }}
        categories={categories}
        onCategoriesChange={(next) => {
          setCategories(next);
        }}
        sort={sort}
        onSortChange={(next) => {
          setSort(next);
        }}
        order={order}
        onOrderChange={(next) => {
          setOrder(next);
        }}
        onDownloadNotice={(format) => {
          // The promise rejection is caught by useNotice's internal error
          // state — we don't surface here so the UI doesn't double-toast.
          void notice.download({ format });
        }}
        isNoticeDownloading={notice.isLoading}
        noticeError={notice.error}
      />

      <div
        className="flex items-center justify-between border-b px-4 py-2 text-xs text-muted-foreground"
        data-testid="obligations-summary"
        data-total={total}
        data-loaded={items.length}
      >
        <span>
          {t("obligations.summary", {
            loaded: items.length,
            total,
          })}
        </span>
      </div>

      {obligations.isError ? (
        <div className="px-6 py-6">
          <Alert variant="destructive" data-testid="obligations-error">
            <AlertDescription>
              {problemMessage(obligations.error, t, {
                action: "obligations.errors.load_list",
              })}
            </AlertDescription>
          </Alert>
        </div>
      ) : null}

      {obligations.isLoading ? (
        <div
          className="flex flex-col gap-2 px-4 py-3"
          data-testid="obligations-loading"
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : null}

      {!obligations.isLoading && !obligations.isError && items.length === 0 ? (
        <EmptyState
          data-testid="obligations-empty"
          className="m-6"
          icon={<CheckCircle2 />}
          title={
            hasNarrowingFilters
              ? t("obligations.empty.filtered_title")
              : t("obligations.empty.title")
          }
          description={
            hasNarrowingFilters
              ? t("obligations.empty.filtered_description")
              : t("obligations.empty.description")
          }
          // Same split the other two grids needed: a filter that excluded
          // everything wants the filter cleared, not a scan.
          action={
            hasNarrowingFilters ? (
              <Button
                variant="outline"
                size="sm"
                onClick={clearAllFilters}
                data-testid="obligations-empty-clear-filters"
              >
                {t("obligations.empty.clear_filters")}
              </Button>
            ) : undefined
          }
        />
      ) : null}

      {!obligations.isLoading && !obligations.isError && items.length > 0 ? (
        // Table semantics for the same reason as the compliance grid: G0-5
        // gave them to the other two grids and left this one announcing an
        // unlabelled stack of divs.
        <div
          className="flex flex-1 flex-col"
          role="table"
          aria-label={t("obligations.table_aria")}
          aria-rowcount={total + 1}
        >
          <ObligationsTableHeader />
          <div
            className="flex-1"
            data-testid="obligations-virtual"
            data-total={total}
            data-loaded={items.length}
          >
            <Virtuoso
              components={VIRTUOSO_TABLE_BODY}
              data={items}
              endReached={() => {
                if (
                  obligations.hasNextPage &&
                  !obligations.isFetchingNextPage
                ) {
                  void obligations.fetchNextPage();
                }
              }}
              style={{
                height: "calc(100vh - var(--layout-header) - 320px)",
              }}
              itemContent={(index, item) => (
                <ObligationRow
                  obligation={item}
                  rowIndex={index}
                  onSelect={() => setDrawerObligation(item.id)}
                />
              )}
            />
          </div>
        </div>
      ) : null}

      <ObligationDrawer
        open={drawerOpen}
        projectId={projectId}
        obligationId={drawerId}
        onOpenChange={(open) => {
          if (!open) setDrawerObligation(null);
        }}
      />
    </div>
  );
}

interface DistributionStripProps {
  distribution: Record<string, number>;
}

function DistributionStrip({ distribution }: DistributionStripProps) {
  const { t } = useTranslation("project_detail");
  const entries = Object.entries(distribution).filter(([, n]) => n > 0);
  if (entries.length === 0) return null;
  return (
    <div
      className="flex flex-wrap items-center gap-2 border-b px-4 py-3"
      data-testid="obligations-distribution"
    >
      <span className="text-xs uppercase tracking-wide text-muted-foreground">
        {t("obligations.distribution.label")}
      </span>
      {entries.map(([kind, count]) => (
        <Badge
          key={kind}
          tone="info"
          data-testid="obligations-distribution-chip"
          data-kind={kind}
          data-count={count}
        >
          {t(`obligations.kind.${kind}`, { defaultValue: kind })}
          <span className="ml-1 font-mono text-[10px] tabular-nums">
            {count}
          </span>
        </Badge>
      ))}
    </div>
  );
}

function ObligationsTableHeader() {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className="flex items-center gap-3 border-b bg-muted/30 px-4 text-xs font-medium uppercase tracking-wide text-muted-foreground"
      style={{ height: "32px" }}
      data-testid="obligations-header"
      role="row"
      aria-rowindex={1}
    >
      <span className="w-44" role="columnheader">
        {t("obligations.column.spdx_id")}
      </span>
      <span className="flex-1" role="columnheader">
        {t("obligations.column.license_name")}
      </span>
      <span className="w-32" role="columnheader">
        {t("obligations.column.category")}
      </span>
      <span className="w-32" role="columnheader">
        {t("obligations.column.kind")}
      </span>
      <span className="w-20 text-right" role="columnheader">
        {t("obligations.column.affected_count")}
      </span>
    </div>
  );
}

interface ObligationRowProps {
  obligation: ObligationListItem;
  rowIndex: number;
  onSelect: () => void;
}

/** Exported for the row-semantics gate, see ComplianceTab for the reason. */
export { ObligationRow as ObligationRowForTest };

function ObligationRow({
  obligation,
  rowIndex,
  onSelect,
}: ObligationRowProps) {
  const { t } = useTranslation("project_detail");
  return (
    // A div, not a button. A `row` owns cells, and `row` is not a role a
    // `<button>` may carry, axe rejects both, and a screen reader in table
    // mode cannot move across a row that has no cells. The keyboard path
    // lives in the first cell instead, the same shape the vulnerabilities
    // row uses.
    <div
      onClick={onSelect}
      data-testid="obligation-row"
      data-obligation-id={obligation.id}
      data-spdx-id={obligation.license_spdx_id ?? ""}
      data-category={obligation.license_category}
      data-kind={obligation.kind}
      data-row-index={rowIndex}
      role="row"
      // +2: the header is row 1, and aria-rowindex counts from 1.
      aria-rowindex={rowIndex + 2}
      className={cn(
        "flex w-full items-center gap-3 border-b px-4 text-left text-sm transition-colors duration-fast ease-out-soft hover:bg-muted/50",
      )}
      style={{ height: "var(--table-row)" }}
    >
      <span className="w-44 truncate" role="cell">
        <button
          type="button"
          data-testid="obligation-row-open"
          onClick={(e) => {
            e.stopPropagation();
            onSelect();
          }}
          title={obligation.license_spdx_id ?? obligation.license_name}
          className="truncate rounded-sm font-mono text-xs hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
        >
          {obligation.license_spdx_id ?? t("licenses.row.no_spdx_id")}
        </button>
      </span>
      <span
        className="flex-1 truncate"
        role="cell"
        title={obligation.license_name}
      >
        {obligation.license_name}
      </span>
      <span className="w-32" role="cell">
        <LicenseCategoryBadge category={obligation.license_category} />
      </span>
      <span
        className="w-32 truncate text-xs text-muted-foreground"
        role="cell"
      >
        {t(`obligations.kind.${obligation.kind}`, {
          defaultValue: obligation.kind,
        })}
      </span>
      <span
        className="w-20 text-right font-mono text-xs tabular-nums"
        role="cell"
        data-testid="obligation-row-affected-count"
      >
        {obligation.affected_count}
      </span>
    </div>
  );
}
