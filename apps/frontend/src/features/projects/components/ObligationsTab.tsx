import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Virtuoso } from "react-virtuoso";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
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
import { ProblemError } from "@/lib/problem";
import { cn } from "@/lib/utils";

/**
 * ObligationsTab — Phase 3 PR #13.
 *
 * Virtualized obligations table + per-kind summary + NOTICE download +
 * drawer for the project detail page. Mirrors `LicensesTab` (PR #12) — read
 * only domain, URL search-param state, debounced search, drawer key
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

function parsePage(raw: string | null): number {
  const n = raw ? Number.parseInt(raw, 10) : 1;
  if (!Number.isFinite(n) || n < 1) return 1;
  return n;
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

  const [search, setSearch] = useState(() => searchParams.get("search") ?? "");
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
  const [page, setPage] = useState<number>(() =>
    parsePage(searchParams.get("page")),
  );

  const drawerId = searchParams.get("obligation");
  const drawerOpen = drawerId != null && drawerId.length > 0;

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
      { replace: true },
    );
  }

  // Debounce search → 300ms.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
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
        if (debouncedSearch) next.set("search", debouncedSearch);
        else next.delete("search");
        if (kinds.length) next.set("kind", kinds.join(","));
        else next.delete("kind");
        if (categories.length)
          next.set("license_category", categories.join(","));
        else next.delete("license_category");
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
    kinds,
    categories,
    sort,
    order,
    page,
    setSearchParams,
  ]);

  const filters = useMemo(
    () => ({
      search: debouncedSearch,
      kinds,
      categories,
      sort,
      order,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
      scanId,
    }),
    [debouncedSearch, kinds, categories, sort, order, page, scanId],
  );

  const obligations = useObligations(projectId, filters);
  const notice = useNotice(projectId, projectName ?? undefined);

  const items: ObligationListItem[] = obligations.data?.items ?? [];
  const total = obligations.data?.total ?? 0;
  const distribution = obligations.data?.distribution ?? {};

  return (
    <div data-testid="obligations-tab" className="flex flex-1 flex-col">
      <DistributionStrip distribution={distribution} />

      <ObligationsToolbar
        search={search}
        onSearchChange={setSearch}
        kinds={kinds}
        onKindsChange={(next) => {
          setKinds(next);
          setPage(1);
        }}
        categories={categories}
        onCategoriesChange={(next) => {
          setCategories(next);
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
              {obligations.error instanceof ProblemError
                ? obligations.error.detail
                : t("obligations.errors.load_list")}
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
        <Card className="m-6" data-testid="obligations-empty">
          <CardHeader>
            <CardTitle className="text-base">
              {t("obligations.empty.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {t("obligations.empty.description")}
          </CardContent>
        </Card>
      ) : null}

      {!obligations.isLoading && !obligations.isError && items.length > 0 ? (
        <>
          <ObligationsTableHeader />
          <div
            className="flex-1"
            data-testid="obligations-virtual"
            data-total={total}
            data-loaded={items.length}
          >
            <Virtuoso
              data={items}
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
        </>
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
    >
      <span className="w-44">{t("obligations.column.spdx_id")}</span>
      <span className="flex-1">{t("obligations.column.license_name")}</span>
      <span className="w-32">{t("obligations.column.category")}</span>
      <span className="w-32">{t("obligations.column.kind")}</span>
      <span className="w-20 text-right">
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

function ObligationRow({
  obligation,
  rowIndex,
  onSelect,
}: ObligationRowProps) {
  const { t } = useTranslation("project_detail");
  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid="obligation-row"
      data-obligation-id={obligation.id}
      data-spdx-id={obligation.license_spdx_id ?? ""}
      data-category={obligation.license_category}
      data-kind={obligation.kind}
      data-row-index={rowIndex}
      className={cn(
        "flex w-full items-center gap-3 border-b px-4 text-left text-sm transition-colors duration-fast ease-out-soft hover:bg-muted/50",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
      )}
      style={{ height: "var(--table-row)" }}
    >
      <span
        className="w-44 truncate font-mono text-xs"
        title={obligation.license_spdx_id ?? obligation.license_name}
      >
        {obligation.license_spdx_id ?? t("licenses.row.no_spdx_id")}
      </span>
      <span className="flex-1 truncate" title={obligation.license_name}>
        {obligation.license_name}
      </span>
      <span className="w-32">
        <LicenseCategoryBadge category={obligation.license_category} />
      </span>
      <span className="w-32 truncate text-xs text-muted-foreground">
        {t(`obligations.kind.${obligation.kind}`, {
          defaultValue: obligation.kind,
        })}
      </span>
      <span
        className="w-20 text-right font-mono text-xs tabular-nums"
        data-testid="obligation-row-affected-count"
      >
        {obligation.affected_count}
      </span>
    </button>
  );
}
