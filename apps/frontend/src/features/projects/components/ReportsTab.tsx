/**
 * ReportsTab — W3 #32 (Reports center).
 *
 * Project-level "where are my downloads" surface. The generation UI for each of
 * the four artefact types deliberately stays on its domain tab (NOTICE on
 * Obligations, SBOM on SBOM, Vulnerability PDF + VEX on Vulnerabilities) so the
 * action lives next to the context that made it relevant. Reports tab is a
 * navigation hub + a single chronological activity table — nothing else.
 *
 * Layout (desktop):
 *
 *   ┌────────────────────────────────┬───────────────────────────────────────┐
 *   │ Generate                       │ Recent activity                       │
 *   │  ┌──────────────┐ ┌──────────┐ │  ┌───────────────────────────────────┐ │
 *   │  │ NOTICE       │ │ SBOM     │ │  │ When / Who / Type / Format / …    │ │
 *   │  ├──────────────┤ ├──────────┤ │  │ rows …                            │ │
 *   │  │ Vuln PDF     │ │ VEX      │ │  │ Pager                             │ │
 *   │  └──────────────┘ └──────────┘ │  └───────────────────────────────────┘ │
 *   └────────────────────────────────┴───────────────────────────────────────┘
 *
 * Mobile collapses to a single column (cards → table).
 *
 * Hard rules followed:
 *   - Every visible string flows through ``t()`` (CLAUDE.md i18n).
 *   - No hex literals — risk-tinted badges use the design tokens via the Badge
 *     ``tone`` variant.
 *   - ``?scan=`` (snapshot pinning) is preserved on every deeplink so a pinned
 *     snapshot does not silently un-pin when the user navigates between tabs.
 *   - 404 is rendered as a generic "Reports unavailable" message — the
 *     backend's existence-hide envelope must not leak permission semantics.
 *   - Color is never the only signal: the type badge pairs a token-tinted
 *     background with a localised label (Notice / SBOM / Vuln PDF / VEX).
 */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { MultiSelect } from "@/components/ui/multi-select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  REPORT_TYPES,
  type ReportDownloadEntry,
  type ReportType,
} from "@/features/projects/api/reportHistoryApi";
import { useReportHistory } from "@/features/projects/api/useReportHistory";
import { ProblemError } from "@/lib/problem";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;

/**
 * Format a byte count as a humanised string ("1.2 MiB"). Kept local rather
 * than promoted to `lib/format` — the helper is one-shot and the disk page
 * has a near-identical copy; if a third caller appears later we lift it.
 *
 * Mirrors `apps/frontend/src/features/admin/disk/AdminDiskPage.tsx::formatBytes`
 * so labels are consistent across the admin surface.
 */
function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || Number.isNaN(bytes)) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

// Each report type maps to (1) the domain tab to deeplink into and (2) a tone
// for the type badge. The tone is a design-token (no hex) — pairs a tinted
// background with the localised label so color is not the only signal.
type ReportTypeUiMeta = {
  /** Target value for `?tab=…` deeplink. */
  targetTab: "obligations" | "sbom" | "vulnerabilities";
  /** Badge tone (design-token via the cva variant). */
  tone: "info" | "low" | "high" | "medium";
  /** Stable test-id slug used by `reports-card-*` + `reports-history-*`. */
  slug: "notice" | "sbom" | "vuln-pdf" | "vex";
};

const REPORT_TYPE_UI: Record<ReportType, ReportTypeUiMeta> = {
  notice: { targetTab: "obligations", tone: "info", slug: "notice" },
  sbom: { targetTab: "sbom", tone: "low", slug: "sbom" },
  vuln_pdf: { targetTab: "vulnerabilities", tone: "high", slug: "vuln-pdf" },
  vex_export: { targetTab: "vulnerabilities", tone: "medium", slug: "vex" },
};

export interface ReportsTabProps {
  projectId: string;
  /**
   * Pinned snapshot scan id (feature #28). When set, deeplinks preserve
   * ``?scan=`` so the user lands on the same snapshot context they had open
   * on Reports. The history list itself is project-wide (not scan-filtered)
   * because users come here to find historical downloads, not the present
   * snapshot's artefacts.
   */
  scanId?: string;
}

export function ReportsTab({ projectId }: ReportsTabProps) {
  const { t, i18n } = useTranslation("project_detail");
  const [searchParams, setSearchParams] = useSearchParams();

  // URL state — page + multi-select type filter persisted as comma-separated
  // tokens so a deep-link survives reload. Mirrors the LicensesTab /
  // ObligationsTab pattern (PR #12 / #13). Page resets to 1 whenever the type
  // filter changes — the next-page boundary is meaningless across filters.
  const pageRaw = searchParams.get("rpt_page");
  const parsedPage = pageRaw ? Number.parseInt(pageRaw, 10) : 1;
  const page = Number.isFinite(parsedPage) && parsedPage >= 1 ? parsedPage : 1;

  const typesRaw = searchParams.get("rpt_type");
  const types = useMemo<ReportType[]>(() => {
    if (!typesRaw) return [];
    const valid = new Set<ReportType>(REPORT_TYPES);
    return typesRaw
      .split(",")
      .map((v) => v.trim())
      .filter((v): v is ReportType => valid.has(v as ReportType));
  }, [typesRaw]);

  const query = useReportHistory(projectId, {
    types,
    page,
    pageSize: PAGE_SIZE,
  });

  function setTypeFilter(next: ReportType[]) {
    setSearchParams(
      (prev) => {
        const merged = new URLSearchParams(prev);
        if (next.length === 0) {
          merged.delete("rpt_type");
        } else {
          merged.set("rpt_type", next.join(","));
        }
        // Switching filters invalidates the current page boundary — go back
        // to page 1 so "no results on page 7" doesn't strand the user.
        merged.delete("rpt_page");
        return merged;
      },
      { replace: true },
    );
  }

  function clearTypeFilter() {
    setTypeFilter([]);
  }

  function setPage(nextPage: number) {
    setSearchParams(
      (prev) => {
        const merged = new URLSearchParams(prev);
        if (nextPage <= 1) {
          merged.delete("rpt_page");
        } else {
          merged.set("rpt_page", String(nextPage));
        }
        return merged;
      },
      { replace: true },
    );
  }

  // Deeplink to a domain tab while preserving `?scan=` (snapshot pinning) and
  // dropping unrelated tab-scoped filter params that the parent ProjectDetail
  // tab handler would also have cleared. We deliberately do NOT clear
  // `rpt_type` / `rpt_page` because returning to the Reports tab should
  // restore the same filter state.
  function deeplinkToTab(target: ReportTypeUiMeta["targetTab"]) {
    setSearchParams(
      (prev) => {
        const merged = new URLSearchParams(prev);
        merged.set("tab", target);
        return merged;
      },
      { replace: false },
    );
  }

  const items = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const pageSize = query.data?.page_size ?? PAGE_SIZE;
  const totalPages = total > 0 ? Math.ceil(total / pageSize) : 1;

  const errorMessage = (() => {
    if (!query.isError) return null;
    const err = query.error;
    if (err instanceof ProblemError) {
      // 404 is the existence-hide envelope (cross-team or missing). Render
      // the generic message so the SPA does not leak permission semantics.
      if (err.status === 404) return t("reports.history.errors.unavailable");
      if (err.status === 429) return t("reports.history.errors.rate_limited");
    }
    return t("reports.history.errors.generic");
  })();

  return (
    <div
      className="flex flex-col gap-6 p-6 lg:flex-row"
      data-testid="reports-tab"
    >
      {/* ---------- Left: generate cards ----------------------------------- */}
      <section
        className="flex flex-col gap-3 lg:w-80 lg:shrink-0"
        aria-labelledby="reports-generate-heading"
        data-testid="reports-generate"
      >
        <div>
          <h2
            id="reports-generate-heading"
            className="text-base font-semibold"
          >
            {t("reports.generate.heading")}
          </h2>
          <p className="text-xs text-muted-foreground">
            {t("reports.generate.subheading")}
          </p>
        </div>
        <GenerateCard
          slug="notice"
          target="obligations"
          onDeeplink={deeplinkToTab}
        />
        <GenerateCard slug="sbom" target="sbom" onDeeplink={deeplinkToTab} />
        <GenerateCard
          slug="vuln-pdf"
          target="vulnerabilities"
          onDeeplink={deeplinkToTab}
        />
        <GenerateCard
          slug="vex"
          target="vulnerabilities"
          onDeeplink={deeplinkToTab}
        />
      </section>

      {/* ---------- Right: history table ----------------------------------- */}
      <section
        className="flex min-w-0 flex-1 flex-col gap-3"
        aria-labelledby="reports-history-heading"
        data-testid="reports-history"
      >
        <div className="flex items-end justify-between gap-3">
          <div>
            <h2
              id="reports-history-heading"
              className="text-base font-semibold"
            >
              {t("reports.history.heading")}
            </h2>
            <p className="text-xs text-muted-foreground">
              {t("reports.history.subheading")}
            </p>
          </div>
          <div className="flex flex-col">
            <label
              htmlFor="reports-history-type-filter"
              className="text-xs font-medium text-muted-foreground"
            >
              {t("reports.history.filter.type_label")}
            </label>
            <div className="flex items-center gap-2">
              <MultiSelect
                id="reports-history-type-filter"
                testId="reports-history-type-filter"
                className="w-40"
                label={t("reports.history.filter.type_label")}
                placeholder={t("reports.history.filter.type_placeholder")}
                options={REPORT_TYPES.map((rt) => ({
                  value: rt,
                  label: t(`reports.history.type.${rt}`),
                }))}
                selected={types}
                onChange={(next) => setTypeFilter(next as ReportType[])}
              />
              {types.length > 0 ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  data-testid="reports-history-type-clear"
                  onClick={clearTypeFilter}
                >
                  {t("reports.history.filter.clear")}
                </Button>
              ) : null}
            </div>
          </div>
        </div>

        {query.isLoading ? (
          <HistorySkeleton />
        ) : errorMessage ? (
          <Alert variant="destructive" data-testid="reports-history-error">
            <AlertDescription>{errorMessage}</AlertDescription>
          </Alert>
        ) : items.length === 0 ? (
          <EmptyState />
        ) : (
          <HistoryTable items={items} locale={i18n.resolvedLanguage} />
        )}

        {!query.isLoading && !errorMessage && total > 0 ? (
          <div
            className="flex items-center justify-between text-xs"
            data-testid="reports-history-pagination"
          >
            <span className="text-muted-foreground">
              {t("reports.history.pagination.page_of", {
                page,
                total: totalPages,
              })}
            </span>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={page <= 1}
                onClick={() => setPage(Math.max(1, page - 1))}
                data-testid="reports-history-prev"
              >
                {t("reports.history.pagination.prev")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={page >= totalPages}
                onClick={() => setPage(Math.min(totalPages, page + 1))}
                data-testid="reports-history-next"
              >
                {t("reports.history.pagination.next")}
              </Button>
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface GenerateCardProps {
  slug: "notice" | "sbom" | "vuln-pdf" | "vex";
  target: ReportTypeUiMeta["targetTab"];
  onDeeplink: (target: ReportTypeUiMeta["targetTab"]) => void;
}

function GenerateCard({ slug, target, onDeeplink }: GenerateCardProps) {
  const { t } = useTranslation("project_detail");
  // i18n keys live under reports.cards.<slug>.{title,description,action}.
  const titleKey = `reports.cards.${slug}.title` as const;
  const descKey = `reports.cards.${slug}.description` as const;
  const actionKey = `reports.cards.${slug}.action` as const;
  return (
    <Card data-testid={`reports-card-${slug}`}>
      <CardHeader className="space-y-1 p-4">
        <CardTitle className="text-sm font-semibold">{t(titleKey)}</CardTitle>
        <CardDescription className="text-xs">{t(descKey)}</CardDescription>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => onDeeplink(target)}
          data-testid={`reports-card-${slug}-deeplink`}
        >
          {t(actionKey)}
        </Button>
      </CardContent>
    </Card>
  );
}

function HistorySkeleton() {
  return (
    <div className="space-y-2" data-testid="reports-history-loading">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

function EmptyState() {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className="flex flex-col items-start gap-1 rounded-md border border-dashed bg-muted/30 p-6"
      data-testid="reports-history-empty"
    >
      <p className="text-sm font-medium">{t("reports.history.empty.title")}</p>
      <p className="text-xs text-muted-foreground">
        {t("reports.history.empty.body")}
      </p>
    </div>
  );
}

interface HistoryTableProps {
  items: ReportDownloadEntry[];
  locale: string | undefined;
}

function HistoryTable({ items, locale }: HistoryTableProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className="overflow-x-auto rounded-md border"
      data-testid="reports-history-table"
    >
      <table className="w-full min-w-[640px] table-fixed text-sm">
        <thead className="border-b bg-muted/30 text-xs uppercase text-muted-foreground">
          <tr className="text-left">
            <th className="px-3 py-2 font-medium">
              {t("reports.history.columns.when")}
            </th>
            <th className="px-3 py-2 font-medium">
              {t("reports.history.columns.who")}
            </th>
            <th className="px-3 py-2 font-medium">
              {t("reports.history.columns.type")}
            </th>
            <th className="px-3 py-2 font-medium">
              {t("reports.history.columns.format")}
            </th>
            <th className="px-3 py-2 font-medium">
              {t("reports.history.columns.scan")}
            </th>
            <th className="px-3 py-2 text-right font-medium">
              {t("reports.history.columns.size")}
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => (
            <HistoryRow key={row.id} row={row} locale={locale} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface HistoryRowProps {
  row: ReportDownloadEntry;
  locale: string | undefined;
}

function HistoryRow({ row, locale }: HistoryRowProps) {
  const { t } = useTranslation("project_detail");
  const meta = REPORT_TYPE_UI[row.report_type];
  // 8-char prefix mirrors how Components / Vulnerabilities surfaces show
  // scan IDs. Tooltip carries the full UUID so the value remains copyable.
  const scanShort = row.scan_id ? row.scan_id.slice(0, 8) : null;
  const whenAbsolute = (() => {
    try {
      return new Date(row.created_at).toLocaleString(locale ?? "en");
    } catch {
      return row.created_at;
    }
  })();
  return (
    <tr
      className={cn("h-10 border-b last:border-b-0")}
      data-testid={`reports-history-row`}
      data-row-id={row.id}
      data-report-type={row.report_type}
    >
      <td className="px-3 py-1.5">
        <span
          className="text-xs text-muted-foreground"
          title={whenAbsolute}
        >
          {formatRelativeToNow(row.created_at, locale)}
        </span>
      </td>
      <td className="px-3 py-1.5">
        <span className="truncate text-xs">
          {row.user
            ? row.user.email
            : t("reports.history.user_unknown")}
        </span>
      </td>
      <td className="px-3 py-1.5">
        <Badge
          tone={meta.tone}
          data-testid="reports-history-type-badge"
          data-report-type={row.report_type}
        >
          {t(`reports.history.type.${row.report_type}`)}
        </Badge>
      </td>
      <td className="px-3 py-1.5">
        <span className="font-mono text-[11px] text-muted-foreground">
          {row.format}
        </span>
      </td>
      <td className="px-3 py-1.5">
        {scanShort ? (
          <span
            className="font-mono text-[11px] text-muted-foreground"
            title={row.scan_id ?? undefined}
          >
            {scanShort}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">
            {t("reports.history.scan_unknown")}
          </span>
        )}
      </td>
      <td className="px-3 py-1.5 text-right">
        <span className="font-mono text-[11px] text-muted-foreground">
          {row.size_bytes != null
            ? formatBytes(row.size_bytes)
            : t("reports.history.size_unknown")}
        </span>
      </td>
    </tr>
  );
}
