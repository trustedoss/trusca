import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Virtuoso } from "react-virtuoso";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useVulnerabilities } from "@/features/projects/api/useVulnerabilities";
import { useVulnReport } from "@/features/projects/api/useVulnReport";
import type {
  SortOrder,
  VulnFindingStatus,
  VulnSeverity,
  VulnerabilityListItem,
  VulnerabilitySortKey,
} from "@/features/projects/api/vulnerabilitiesApi";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";
import { VulnerabilitiesToolbar } from "@/features/projects/components/VulnerabilitiesToolbar";
import { VulnerabilityDrawer } from "@/features/projects/components/VulnerabilityDrawer";
import { VulnerabilityStatusBadge } from "@/features/projects/components/VulnerabilityStatusBadge";
import {
  EPSS_EMPTY,
  formatEpssPercentile,
  formatEpssScore,
} from "@/features/projects/lib/epss";
import { ALL_VULNERABILITY_STATUSES } from "@/features/projects/lib/vulnerabilityTransitions";
import { ProblemError } from "@/lib/problem";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

/**
 * VulnerabilitiesTab — Phase 3 PR #11.
 *
 * Virtualized vulnerability findings table + drawer for the project detail
 * page. Mirrors the structure of `ComponentsTab`:
 *
 *   - `useVulnerabilities` is a paginated `useQuery` keyed on the entire
 *     filter tuple. Filter or sort changes naturally invalidate the cached
 *     page and refetch from offset 0.
 *   - Search input is debounced 300ms before it hits the query.
 *   - Filters, sort, and the selected drawer finding id are mirrored into
 *     URL search params (deep-link + reload survival). The drawer key is
 *     `?vuln=<finding_id>` so it doesn't collide with ComponentsTab's
 *     `?drawer=<component_id>` (PR #10).
 *   - Virtuoso renders fixed 40px rows (CLAUDE.md compact density).
 *
 * Pagination is offset/limit (not cursor) because PATCH writes a full detail
 * payload back into a single page cache; cursor pages would need
 * reconciliation across multiple cached chunks.
 */

const PAGE_SIZE = 100;

const VALID_SEVERITY = new Set<VulnSeverity>([
  "critical",
  "high",
  "medium",
  "low",
  "info",
  "unknown",
]);

const VALID_STATUS = new Set<VulnFindingStatus>(ALL_VULNERABILITY_STATUSES);

const VALID_SORT = new Set<VulnerabilitySortKey>([
  "severity",
  "cvss",
  "status",
  "discovered_at",
  "epss",
]);

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

function parseSort(raw: string | null): VulnerabilitySortKey {
  if (raw && VALID_SORT.has(raw as VulnerabilitySortKey)) {
    return raw as VulnerabilitySortKey;
  }
  return "severity";
}

function parseOrder(raw: string | null): SortOrder {
  return raw === "asc" ? "asc" : "desc";
}

function parsePage(raw: string | null): number {
  const n = raw ? Number.parseInt(raw, 10) : 1;
  if (!Number.isFinite(n) || n < 1) return 1;
  return n;
}

/**
 * Parse the `min_epss` URL param into a [0, 1] threshold, or `null` for "no
 * threshold". Out-of-range / non-numeric values fall back to null so a hand
 * edited URL can't wedge the filter.
 */
function parseMinEpss(raw: string | null): number | null {
  if (raw == null || raw.length === 0) return null;
  const n = Number.parseFloat(raw);
  if (!Number.isFinite(n) || n < 0 || n > 1) return null;
  return n;
}

export interface VulnerabilitiesTabProps {
  projectId: string;
  /** Used to build the PDF report download filename fallback (G2). */
  projectName?: string | null;
}

export function VulnerabilitiesTab({
  projectId,
  projectName,
}: VulnerabilitiesTabProps) {
  const { t, i18n } = useTranslation("project_detail");
  const [searchParams, setSearchParams] = useSearchParams();

  // ----- filter state, hydrated from URL on first render -------------------
  const [search, setSearch] = useState(() => searchParams.get("search") ?? "");
  const [debouncedSearch, setDebouncedSearch] = useState(search);
  const [severity, setSeverity] = useState<VulnSeverity[]>(() =>
    parseList<VulnSeverity>(searchParams.get("severity"), VALID_SEVERITY),
  );
  const [status, setStatus] = useState<VulnFindingStatus[]>(() =>
    parseList<VulnFindingStatus>(searchParams.get("status"), VALID_STATUS),
  );
  const [sort, setSort] = useState<VulnerabilitySortKey>(() =>
    parseSort(searchParams.get("sort")),
  );
  const [order, setOrder] = useState<SortOrder>(() =>
    parseOrder(searchParams.get("order")),
  );
  const [minEpss, setMinEpss] = useState<number | null>(() =>
    parseMinEpss(searchParams.get("min_epss")),
  );
  const [page, setPage] = useState<number>(() =>
    parsePage(searchParams.get("page")),
  );

  // Drawer state — `?vuln=<finding_id>` so reload restores the selection.
  const drawerId = searchParams.get("vuln");
  const drawerOpen = drawerId != null && drawerId.length > 0;

  function setDrawerVuln(findingId: string | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (findingId) {
          next.set("vuln", findingId);
        } else {
          next.delete("vuln");
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
        if (severity.length) next.set("severity", severity.join(","));
        else next.delete("severity");
        if (status.length) next.set("status", status.join(","));
        else next.delete("status");
        if (sort !== "severity") next.set("sort", sort);
        else next.delete("sort");
        if (order !== "desc") next.set("order", order);
        else next.delete("order");
        if (minEpss != null) next.set("min_epss", String(minEpss));
        else next.delete("min_epss");
        if (page !== 1) next.set("page", String(page));
        else next.delete("page");
        return next;
      },
      { replace: true },
    );
  }, [
    debouncedSearch,
    severity,
    status,
    sort,
    order,
    minEpss,
    page,
    setSearchParams,
  ]);

  const filters = useMemo(
    () => ({
      search: debouncedSearch,
      severity,
      status,
      sort,
      order,
      min_epss: minEpss,
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    }),
    [debouncedSearch, severity, status, sort, order, minEpss, page],
  );

  const vulnerabilities = useVulnerabilities(projectId, filters);
  const vulnReport = useVulnReport(projectId, projectName);

  const items: VulnerabilityListItem[] = vulnerabilities.data?.items ?? [];
  const total = vulnerabilities.data?.total ?? 0;

  return (
    <div data-testid="vulnerabilities-tab" className="flex flex-1 flex-col">
      <VulnerabilitiesToolbar
        search={search}
        onSearchChange={setSearch}
        severity={severity}
        onSeverityChange={(next) => {
          setSeverity(next);
          setPage(1);
        }}
        status={status}
        onStatusChange={(next) => {
          setStatus(next);
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
        minEpss={minEpss}
        onMinEpssChange={(next) => {
          setMinEpss(next);
          setPage(1);
        }}
        onDownloadPdf={() => {
          // The rejection is captured by useVulnReport's internal error state
          // and rendered inline in the toolbar — swallow it here so it doesn't
          // bubble up as an unhandled promise rejection.
          vulnReport.download().catch(() => {
            /* error already surfaced via vulnReport.error */
          });
        }}
        isPdfDownloading={vulnReport.isLoading}
        pdfError={vulnReport.error}
      />

      <div
        className="flex items-center justify-between border-b px-4 py-2 text-xs text-muted-foreground"
        data-testid="vulnerabilities-summary"
        data-total={total}
        data-loaded={items.length}
      >
        <span>
          {t("vulnerabilities.summary", {
            loaded: items.length,
            total,
          })}
        </span>
      </div>

      {vulnerabilities.isError ? (
        <div className="px-6 py-6">
          <Alert variant="destructive" data-testid="vulnerabilities-error">
            <AlertDescription>
              {vulnerabilities.error instanceof ProblemError
                ? vulnerabilities.error.detail
                : t("vulnerabilities.errors.load_failed")}
            </AlertDescription>
          </Alert>
        </div>
      ) : null}

      {vulnerabilities.isLoading ? (
        <div
          className="flex flex-col gap-2 px-4 py-3"
          data-testid="vulnerabilities-loading"
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : null}

      {!vulnerabilities.isLoading &&
      !vulnerabilities.isError &&
      items.length === 0 ? (
        <Card className="m-6" data-testid="vulnerabilities-empty">
          <CardHeader>
            <CardTitle className="text-base">
              {t("vulnerabilities.empty.title")}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {t("vulnerabilities.empty.subtitle")}
          </CardContent>
        </Card>
      ) : null}

      {!vulnerabilities.isLoading &&
      !vulnerabilities.isError &&
      items.length > 0 ? (
        <>
          <VulnerabilitiesTableHeader />
          <div
            className="flex-1"
            data-testid="vulnerabilities-virtual"
            data-total={total}
            data-loaded={items.length}
          >
            <Virtuoso
              data={items}
              style={{
                height: "calc(100vh - var(--layout-header) - 240px)",
              }}
              itemContent={(index, item) => (
                <VulnerabilityRow
                  vulnerability={item}
                  rowIndex={index}
                  locale={i18n.language}
                  onSelect={() => setDrawerVuln(item.id)}
                />
              )}
            />
          </div>
        </>
      ) : null}

      <VulnerabilityDrawer
        open={drawerOpen}
        findingId={drawerId}
        onOpenChange={(open) => {
          if (!open) setDrawerVuln(null);
        }}
      />
    </div>
  );
}

function VulnerabilitiesTableHeader() {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className="flex items-center gap-3 border-b bg-muted/30 px-4 text-xs font-medium uppercase tracking-wide text-muted-foreground"
      style={{ height: "32px" }}
      data-testid="vulnerabilities-header"
    >
      <span className="w-44">{t("vulnerabilities.column.cve_id")}</span>
      <span className="w-28">{t("vulnerabilities.column.severity")}</span>
      <span className="w-16 text-right">
        {t("vulnerabilities.column.cvss")}
      </span>
      <span
        className="w-20 text-right"
        title={t("vulnerabilities.epss.tooltip", {
          defaultValue:
            "EPSS — probability this CVE is exploited in the wild within 30 days. Complements CVSS (severity).",
        })}
      >
        {t("vulnerabilities.column.epss", { defaultValue: "EPSS" })}
      </span>
      <span className="flex-1">{t("vulnerabilities.column.summary")}</span>
      <span className="w-20 text-right">
        {t("vulnerabilities.column.affected")}
      </span>
      <span className="w-32">{t("vulnerabilities.column.status")}</span>
      <span className="w-32">{t("vulnerabilities.column.discovered")}</span>
    </div>
  );
}

interface VulnerabilityRowProps {
  vulnerability: VulnerabilityListItem;
  rowIndex: number;
  locale: string;
  onSelect: () => void;
}

function VulnerabilityRow({
  vulnerability,
  rowIndex,
  locale,
  onSelect,
}: VulnerabilityRowProps) {
  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid="vulnerability-row"
      data-finding-id={vulnerability.id}
      data-cve-id={vulnerability.cve_id}
      data-row-index={rowIndex}
      className={cn(
        "flex w-full items-center gap-3 border-b px-4 text-left text-sm hover:bg-muted/50",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
      )}
      style={{ height: "var(--table-row)" }}
    >
      <span
        className="w-44 truncate font-mono text-xs"
        title={vulnerability.cve_id}
      >
        {vulnerability.cve_id}
      </span>
      <span className="w-28">
        <SeverityBadge severity={vulnerability.severity} />
      </span>
      <span
        className="w-16 text-right font-mono text-xs tabular-nums"
        data-testid="vulnerability-row-cvss"
      >
        {vulnerability.cvss_score != null
          ? vulnerability.cvss_score.toFixed(1)
          : "—"}
      </span>
      <EpssCell
        score={vulnerability.epss_score}
        percentile={vulnerability.epss_percentile}
      />
      <span
        className="flex-1 truncate"
        title={vulnerability.summary ?? ""}
      >
        {vulnerability.summary ?? "—"}
      </span>
      <span
        className="w-20 text-right font-mono text-xs tabular-nums"
        data-testid="vulnerability-row-affected"
      >
        {vulnerability.affected_component_count}
      </span>
      <span className="w-32">
        <VulnerabilityStatusBadge status={vulnerability.status} />
      </span>
      <span
        className="w-32 truncate text-xs text-muted-foreground"
        title={vulnerability.discovered_at}
      >
        {formatRelativeToNow(vulnerability.discovered_at, locale)}
      </span>
    </button>
  );
}

interface EpssCellProps {
  score: number | null;
  percentile: number | null;
}

/**
 * EPSS table cell. Renders the score as a one-decimal percentage in the mono
 * accent font and folds the percentile ("Top N%") into the title tooltip so
 * the compact 40px row stays narrow. A missing score renders the em-dash
 * placeholder — never "0%".
 */
function EpssCell({ score, percentile }: EpssCellProps) {
  const { t } = useTranslation("project_detail");
  const formattedScore = formatEpssScore(score);
  const formattedPercentile = formatEpssPercentile(percentile);

  if (formattedScore == null) {
    return (
      <span
        className="w-20 text-right font-mono text-xs tabular-nums text-muted-foreground"
        data-testid="vulnerability-row-epss"
        data-epss-empty="true"
        title={t("vulnerabilities.epss.empty", {
          defaultValue: "No EPSS data for this CVE",
        })}
      >
        {EPSS_EMPTY}
      </span>
    );
  }

  // Percentile becomes the tooltip — "97.3% · Top 9%" — so triagers can read
  // the rank without widening the column.
  const tooltip =
    formattedPercentile != null
      ? t("vulnerabilities.epss.cell_tooltip", {
          score: formattedScore,
          percentile: formattedPercentile,
          defaultValue: "{{score}} · {{percentile}} most likely to be exploited",
        })
      : formattedScore;

  return (
    <span
      className="w-20 text-right font-mono text-xs tabular-nums"
      data-testid="vulnerability-row-epss"
      data-epss-score={score ?? undefined}
      title={tooltip}
    >
      {formattedScore}
    </span>
  );
}
