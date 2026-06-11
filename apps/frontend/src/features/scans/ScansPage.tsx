/**
 * ScansPage — Phase 3 / Step 4-C.
 *
 * Cross-project scan queue scoped to the current user's reachable teams.
 * Five tabs (Running / Queued / Succeeded / Failed / All) drive a status
 * filter on `GET /v1/scans`. The table is compact (40 px rows) and is
 * paginated 20-per-page (the backend caps `size` at 100 but we stay small
 * to keep the queue feel snappy).
 *
 * Project name isn't returned by the list endpoint (the backend ships
 * `ScanPublic` with `project_id` only), so the column shows the first
 * eight characters of the UUID with a `font-mono` style — same convention
 * AdminScansPage uses for the scan id column.
 */
import { Activity } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TableRowsSkeleton } from "@/components/ui/skeletons";
import { ScanCancelButton } from "@/features/scans/ScanCancelButton";
import { useScans } from "@/features/scans/useScans";
import RelativeTime from "@/components/RelativeTime";
import { cn } from "@/lib/utils";
import { type ScanPublic, type ScanStatus } from "@/lib/projectsApi";

const PAGE_SIZE = 20;

type ScansTab = "running" | "queued" | "succeeded" | "failed" | "all";

const TABS: ScansTab[] = ["running", "queued", "succeeded", "failed", "all"];

const TAB_TO_STATUS: Record<ScansTab, ScanStatus | undefined> = {
  running: "running",
  queued: "queued",
  succeeded: "succeeded",
  failed: "failed",
  all: undefined,
};

function statusTone(
  status: ScanStatus,
): "running" | "queued" | "succeeded" | "failed" | "cancelled" {
  return status;
}

function StatusBadge({ status }: { status: ScanStatus }) {
  const { t } = useTranslation("scans");
  const tone = statusTone(status);
  return (
    <Badge
      variant="outline"
      data-testid="scans-status-badge"
      data-status={status}
      data-tone={tone}
      className={cn(
        "gap-1 font-mono text-xs",
        tone === "succeeded" &&
          "border-emerald-300 bg-emerald-50 text-emerald-700",
        tone === "running" && "border-blue-300 bg-blue-50 text-blue-700",
        tone === "queued" && "border-amber-300 bg-amber-50 text-amber-700",
        tone === "failed" && "border-red-300 bg-red-50 text-red-700",
        tone === "cancelled" &&
          "border-muted bg-muted text-muted-foreground",
      )}
    >
      {t(`page.status.${status}`)}
    </Badge>
  );
}

function durationSeconds(scan: ScanPublic): number | null {
  if (!scan.started_at) return null;
  const start = Date.parse(scan.started_at);
  const end = scan.completed_at ? Date.parse(scan.completed_at) : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return Math.max(0, Math.round((end - start) / 1000));
}

export function ScansPage() {
  const { t, i18n } = useTranslation("scans");

  // P2 #4 — accept `?status=running|queued|succeeded|failed|all` so the
  // Dashboard StatCards can deep-link straight into the matching tab.
  // Default tab stays "all" so the page itself opens unchanged when there
  // are no params.
  const [searchParams, setSearchParams] = useSearchParams();
  const statusParam = searchParams.get("status");
  const initialTab: ScansTab = (TABS as readonly string[]).includes(
    statusParam ?? "",
  )
    ? (statusParam as ScansTab)
    : "all";

  const [tab, setTab] = useState<ScansTab>(initialTab);
  const [page, setPage] = useState(1);

  const queryParams = useMemo(
    () => ({
      status: TAB_TO_STATUS[tab],
      page,
      size: PAGE_SIZE,
    }),
    [tab, page],
  );

  const scansQuery = useScans(queryParams);
  const items = scansQuery.data?.items ?? [];
  const total = scansQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function changeTab(next: ScansTab) {
    setTab(next);
    setPage(1);
    // Mirror into the URL so refresh / share preserves the active tab. The
    // "all" tab clears the param (default state should not carry noise).
    setSearchParams(
      (prev) => {
        const merged = new URLSearchParams(prev);
        if (next === "all") {
          merged.delete("status");
        } else {
          merged.set("status", next);
        }
        return merged;
      },
      { replace: false },
    );
  }

  return (
    <div className="flex h-full flex-col" data-testid="scans-page">
      <PageHeader
        title={t("page.title")}
        description={t("page.subtitle")}
      />

      <div
        className="flex flex-wrap items-center gap-2 border-b bg-card px-6 py-2"
        data-testid="scans-tabs"
        role="tablist"
      >
        {TABS.map((value) => (
          <Button
            key={value}
            size="sm"
            variant={tab === value ? "default" : "outline"}
            onClick={() => changeTab(value)}
            role="tab"
            aria-selected={tab === value}
            data-testid={`scans-tab-${value}`}
            data-active={tab === value}
          >
            {t(`page.tab.${value}`)}
          </Button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto">
        {scansQuery.isError ? (
          <div className="px-6 py-4">
            <Alert variant="destructive" data-testid="scans-error">
              <AlertDescription>{t("page.error")}</AlertDescription>
            </Alert>
          </div>
        ) : null}

        <table
          className="w-full text-sm"
          data-testid="scans-table"
          aria-busy={scansQuery.isLoading}
        >
          <thead className="sticky top-0 bg-card">
            <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-6 py-2">{t("page.column.project")}</th>
              <th className="px-3 py-2">{t("page.column.kind")}</th>
              <th className="px-3 py-2">{t("page.column.status")}</th>
              <th className="px-3 py-2">{t("page.column.started")}</th>
              <th className="px-3 py-2 text-right">
                {t("page.column.duration")}
              </th>
              <th className="px-3 py-2 text-right">
                {t("page.column.actions")}
              </th>
            </tr>
          </thead>
          <tbody data-testid="scans-tbody">
            {scansQuery.isLoading
              ? (
                  <TableRowsSkeleton
                    columns={["w-40", "w-16", "w-20", "w-24", "w-12", "w-16"]}
                  />
                )
              : items.map((scan) => {
                  const dur = durationSeconds(scan);
                  return (
                    <tr
                      key={scan.id}
                      data-testid="scans-row"
                      data-scan-id={scan.id}
                      data-status={scan.status}
                      className="border-b transition-colors duration-fast ease-out-soft hover:bg-accent/40"
                      style={{ height: "var(--table-row)" }}
                    >
                      <td className="px-6 text-xs">
                        <div className="flex items-center gap-2">
                          {/* P1 #5 — show the project name + link to
                              /projects/{id} when the BE surfaced it, fall
                              back to the legacy 8-char UUID otherwise so
                              older snapshots (or the single-row endpoints
                              that don't ship project_name yet) still
                              render. */}
                          {scan.project_name ? (
                            <Link
                              to={`/projects/${scan.project_id}`}
                              className="truncate font-medium text-foreground hover:underline"
                              data-testid="scans-row-project-link"
                            >
                              {scan.project_name}
                            </Link>
                          ) : (
                            <span className="truncate font-mono">
                              {scan.project_id.slice(0, 8)}
                            </span>
                          )}
                          {scan.release ? (
                            <span
                              className="inline-flex shrink-0 items-center rounded border border-border bg-muted px-1.5 py-0.5 text-[11px] font-medium text-foreground"
                              data-testid="scans-row-release"
                              data-release={scan.release}
                              title={t("release.chip_aria", {
                                release: scan.release,
                              })}
                            >
                              {scan.release}
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td className="px-3">
                        <Badge
                          variant="outline"
                          className="bg-muted text-xs text-muted-foreground"
                        >
                          {t(`page.kind.${scan.kind}`)}
                        </Badge>
                      </td>
                      <td className="px-3">
                        <StatusBadge status={scan.status} />
                      </td>
                      <td className="px-3 text-xs text-muted-foreground">
                        {scan.started_at ? (
                          <RelativeTime
                            value={scan.started_at}
                            locale={i18n.resolvedLanguage}
                          />
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-3 text-right text-xs text-muted-foreground">
                        {dur == null ? "—" : `${dur}s`}
                      </td>
                      <td className="px-3 text-right">
                        <div className="flex justify-end">
                          <ScanCancelButton
                            scanId={scan.id}
                            status={scan.status}
                          />
                        </div>
                      </td>
                    </tr>
                  );
                })}
            {!scansQuery.isLoading && items.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-0">
                  <EmptyState
                    data-testid="scans-empty"
                    icon={<Activity />}
                    title={t("page.empty")}
                  />
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <footer
        className="flex shrink-0 items-center justify-between border-t bg-card px-6 py-2 text-xs"
        data-testid="scans-pagination"
      >
        <span className="text-muted-foreground">
          {t("page.pagination.summary", { page, total: totalPages })}
        </span>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            data-testid="scans-page-prev"
          >
            {t("page.pagination.previous")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            data-testid="scans-page-next"
          >
            {t("page.pagination.next")}
          </Button>
        </div>
      </footer>
    </div>
  );
}
