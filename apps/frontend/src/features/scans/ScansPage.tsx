// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
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
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TableRowsSkeleton } from "@/components/ui/skeletons";
import { ScanCancelButton } from "@/features/scans/ScanCancelButton";
import { useScans } from "@/features/scans/useScans";
import { useClampPage, usePageParam, useUrlEnum } from "@/hooks/useUrlState";
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
        // Status surface tokens (G0-1) — the shade a scan pill uses is a
        // design-system decision, not a per-file one.
        tone === "succeeded" &&
          "border-status-success-border bg-status-success-subtle text-status-success-foreground",
        tone === "running" &&
          "border-status-info-border bg-status-info-subtle text-status-info-foreground",
        tone === "queued" &&
          "border-status-warning-border bg-status-warning-subtle text-status-warning-foreground",
        tone === "failed" &&
          "border-status-danger-border bg-status-danger-subtle text-status-danger-foreground",
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
  //
  // B1: the tab used to be seeded from the URL once and written back on
  // change, so pressing Back moved the address bar and left the tab where it
  // was. It now reads the URL every render, and the page joins it.
  const [tab, setTab] = useUrlEnum<ScansTab>("status", TABS, "all");
  const [page, setPage] = usePageParam();

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
  // The page is now something a link or a bookmark can carry, and it can
  // name a page this list no longer has.
  useClampPage(page, totalPages, setPage, scansQuery.isSuccess);

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
            onClick={() => setTab(value)}
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
            {!scansQuery.isLoading &&
            !scansQuery.isError &&
            items.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-0">
                  {/* C3 - the "all" tab sends no status filter, so on a fresh
                      deployment the filter copy blamed a control nobody had
                      touched and sent the reader looking for it. The two
                      states also want different things: one wants the filter
                      widened, the other wants a scan to exist at all, and a
                      scan starts from a project rather than here. */}
                  <EmptyState
                    data-testid="scans-empty"
                    icon={<Activity />}
                    title={tab === "all" ? t("page.empty_all") : t("page.empty")}
                    description={
                      tab === "all" ? t("page.empty_all_hint") : undefined
                    }
                    action={
                      tab === "all" ? (
                        <Button
                          size="sm"
                          variant="outline"
                          asChild
                          data-testid="scans-empty-projects"
                        >
                          <Link to="/projects">{t("page.empty_all_cta")}</Link>
                        </Button>
                      ) : undefined
                    }
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
