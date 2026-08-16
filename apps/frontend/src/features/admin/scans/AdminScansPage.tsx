// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * AdminScansPage — Phase 4 PR #14 §4.5.
 *
 * Compact 40px-row table fed by `useAdminScans`. Four tabs select the
 * status filter — running / queued / failed / all. Next to the tabs sit
 * two server-side filters (M-35): a scan-kind select and a debounced
 * project-name search. Clicking a row opens `AdminScanDrawer` with the
 * cancel affordance.
 *
 * The query polls every 30s so an operator who lands on the page sees
 * the queue update without a manual refresh; the polling interval is the
 * only "live" surface — full WebSocket subscription is in scope of a
 * future PR (the existing `useScanWebSocket` hook is per-scan and the
 * cross-team queue would require a fan-out we don't ship yet).
 */
import { RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { AdminScanDrawer, ScanStatusBadge } from "@/features/admin/scans/AdminScanDrawer";
import {
  type AdminScanListItem,
  type AdminScanStatus,
} from "@/features/admin/scans/api/adminScansApi";
import { useAdminScans } from "@/features/admin/scans/api/useAdminScans";
import RelativeTime from "@/components/RelativeTime";
import {
  useClampPage,
  usePageParam,
  useUrlEnum,
  useUrlParam,
  useUrlText,
} from "@/hooks/useUrlState";
import { cn } from "@/lib/utils";
import { SCAN_KIND_VALUES, type ScanKind } from "@/lib/projectsApi";

const PAGE_SIZE_OPTIONS = [25, 50, 100] as const;

const KIND_OPTIONS: ScanKind[] = [...SCAN_KIND_VALUES];

type ScansTab = "running" | "queued" | "failed" | "all";

const TAB_TO_STATUS: Record<ScansTab, AdminScanStatus | null> = {
  running: "running",
  queued: "queued",
  failed: "failed",
  all: null,
};

const TABS: ScansTab[] = ["running", "queued", "failed", "all"];

export function AdminScansPage() {
  const { t, i18n } = useTranslation("admin");

  // B1: every filter lives in the address bar, so a reload or a Back returns
  // the operator to the list they were reading, and a URL sent to a colleague
  // shows them the same scans.
  const [tab, setTab] = useUrlEnum<ScansTab>("status", TABS, "running");
  const [kindFilter, setKindFilter] = useUrlEnum<ScanKind | "all">(
    "kind",
    ["all", ...KIND_OPTIONS],
    "all",
  );
  const [projectDebounced, setProjectDebounced] = useUrlText("project");
  const [projectInput, setProjectInput] = useState(projectDebounced);
  const [page, setPage] = usePageParam();
  const [pageSize, setPageSize] = useUrlParam<
    (typeof PAGE_SIZE_OPTIONS)[number]
  >("size", {
    parse: (raw) => {
      const n = Number.parseInt(raw ?? "", 10);
      return (PAGE_SIZE_OPTIONS as readonly number[]).includes(n)
        ? (n as (typeof PAGE_SIZE_OPTIONS)[number])
        : 50;
    },
    serialize: (value) => (value === 50 ? null : String(value)),
  });
  // The drawer carries the scan id, not the row: the row is whatever the
  // list holds. Because the filters are in the URL too, the same URL rebuilds
  // the same list, so the id resolves again on reload. On a fresh mount a
  // scan that has since left that list cannot be resolved at all, so the
  // drawer stays shut. A drawer already open when its scan leaves is a
  // different case and is handled below: it stays open.
  const [openScanId, setOpenScanId] = useUrlParam<string | null>("scan", {
    parse: (raw) => raw || null,
    serialize: (value) => value,
    resetsPage: false,
  });

  // Back moves the URL; the field has to follow it, or it shows a term the
  // list is no longer filtered by.
  //
  // Only when the field is not already showing that term. The URL holds the
  // trimmed form, so following it unconditionally would swallow a trailing
  // space 300ms after the operator typed it, and their next word would join
  // the previous one.
  useEffect(() => {
    setProjectInput((current) =>
      current.trim() === projectDebounced ? current : projectDebounced,
    );
  }, [projectDebounced]);

  // 300ms debounce on the project-name search — same pattern as the audit
  // page's text filters. The debounced commit also rewinds to page 1. The
  // equality guard keeps the mount render (and the settled state) from
  // scheduling a spurious page-1 reset.
  useEffect(() => {
    if (projectInput.trim() === projectDebounced) return undefined;
    const id = setTimeout(() => {
      setProjectDebounced(projectInput);
    }, 300);
    return () => clearTimeout(id);
  }, [projectInput, projectDebounced, setProjectDebounced]);

  const queryParams = useMemo(
    () => ({
      page,
      page_size: pageSize,
      status: TAB_TO_STATUS[tab],
      kind: kindFilter === "all" ? null : kindFilter,
      project: projectDebounced.trim() || null,
    }),
    [page, pageSize, tab, kindFilter, projectDebounced],
  );

  const scansQuery = useAdminScans(queryParams);
  // Memoised because the drawer effect below depends on it, and a bare
  // `?? []` is a new array on every render while the query is in flight.
  const items = useMemo(() => scansQuery.data?.items ?? [], [scansQuery.data]);
  const total = scansQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  // The page is now something a link or a bookmark can carry, and it can
  // name a page this list no longer has.
  useClampPage(page, totalPages, setPage, scansQuery.isSuccess);

  // The drawer keeps the row it was opened with rather than re-reading it
  // from the list on every render. This list polls every 30 seconds and the
  // cancel action invalidates it, and the default tab is `running`: a scan
  // being read finishes, leaves the list, and the drawer would vanish from
  // under the operator with `?scan=` still in the address bar. Radix does
  // not call `onOpenChange` when a prop closes it, so nothing would clear
  // the parameter either.
  const [openScan, setOpenScan] = useState<AdminScanListItem | null>(null);
  useEffect(() => {
    if (!openScanId) {
      setOpenScan(null);
      return;
    }
    // Present means fresh. Absent means keep what is on screen, but only if
    // it is the scan the URL still names. Following a link to a different
    // scan that is not in this list must not leave the previous one open.
    const found = items.find((scan) => scan.id === openScanId);
    if (found) setOpenScan(found);
    else setOpenScan((current) => (current?.id === openScanId ? current : null));
  }, [openScanId, items]);

  return (
    <div className="flex h-full flex-col" data-testid="admin-scans-page">
      <PageHeader
        title={t("admin.scans.title")}
        description={t("admin.scans.subtitle")}
      />

      <div
        className="flex flex-wrap items-center gap-2 border-b bg-card px-6 py-2"
        data-testid="admin-scans-tabs"
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
            data-testid={`admin-scans-tab-${value}`}
            data-active={tab === value}
          >
            {t(`admin.scans.tabs.${value}`)}
          </Button>
        ))}
        <select
          aria-label={t("admin.scans.filter.kind_label")}
          data-testid="admin-scans-kind"
          className={cn(
            "h-8 rounded-md border border-input bg-background px-2 text-sm",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          )}
          value={kindFilter}
          // The hook clears the page itself when a filter moves.
          onChange={(e) => setKindFilter(e.target.value as ScanKind | "all")}
        >
          <option value="all">{t("admin.scans.filter.kind_all")}</option>
          {KIND_OPTIONS.map((value) => (
            <option key={value} value={value}>
              {t(`admin.scans.filter.kind.${value}`)}
            </option>
          ))}
        </select>
        <Input
          aria-label={t("admin.scans.filter.project_label")}
          data-testid="admin-scans-project"
          className="h-8 w-56"
          value={projectInput}
          placeholder={t("admin.scans.filter.project_placeholder")}
          onChange={(e) => setProjectInput(e.target.value)}
          // Matches the bound the URL applies, so a paste is refused at the
          // field rather than silently truncated 300ms later.
          maxLength={200}
        />
        <div className="ml-auto">
          <Button
            size="sm"
            variant="outline"
            onClick={() => scansQuery.refetch()}
            disabled={scansQuery.isFetching}
            data-testid="admin-scans-refresh"
          >
            <RefreshCw
              className={cn(
                "h-4 w-4",
                scansQuery.isFetching && "animate-spin",
              )}
              aria-hidden
            />
            {t("admin.scans.actions.refresh")}
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {scansQuery.isError ? (
          <div className="px-6 py-4">
            <Alert variant="destructive" data-testid="admin-scans-error">
              <AlertDescription>{t("admin.errors.unknown")}</AlertDescription>
            </Alert>
          </div>
        ) : null}

        <table
          className="w-full text-sm"
          data-testid="admin-scans-table"
          aria-busy={scansQuery.isLoading}
        >
          <thead className="sticky top-0 bg-card">
            <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-6 py-2">{t("admin.scans.column.id")}</th>
              <th className="px-3 py-2">{t("admin.scans.column.project")}</th>
              <th className="px-3 py-2">{t("admin.scans.column.team")}</th>
              <th className="px-3 py-2">{t("admin.scans.column.status")}</th>
              <th className="px-3 py-2">{t("admin.scans.column.kind")}</th>
              <th className="px-3 py-2">{t("admin.scans.column.started_at")}</th>
              <th className="px-3 py-2 text-right">
                {t("admin.scans.column.duration")}
              </th>
            </tr>
          </thead>
          <tbody data-testid="admin-scans-tbody">
            {scansQuery.isLoading
              ? Array.from({ length: 6 }).map((_, i) => (
                  <tr key={`skeleton-${i}`} className="border-b">
                    <td className="px-6 py-2" colSpan={7}>
                      <Skeleton className="h-5 w-full" />
                    </td>
                  </tr>
                ))
              : items.map((scan) => (
                  <tr
                    key={scan.id}
                    data-testid="admin-scans-row"
                    data-scan-id={scan.id}
                    data-status={scan.status}
                    className="cursor-pointer border-b transition-colors duration-fast ease-out-soft hover:bg-accent/40 focus-within:bg-accent/40"
                    style={{ height: "var(--table-row)" }}
                    tabIndex={0}
                    onClick={() => setOpenScanId(scan.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setOpenScanId(scan.id);
                      }
                    }}
                  >
                    <td className="truncate px-6 font-mono text-xs">
                      {scan.id.slice(0, 8)}
                    </td>
                    <td className="truncate px-3">{scan.project_name}</td>
                    <td className="truncate px-3 text-xs text-muted-foreground">
                      {scan.team_name}
                    </td>
                    <td className="px-3">
                      <ScanStatusBadge status={scan.status} />
                    </td>
                    <td className="px-3">
                      <Badge
                        variant="outline"
                        className="bg-muted text-xs text-muted-foreground"
                      >
                        {scan.kind}
                      </Badge>
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
                      {scan.duration_seconds == null
                        ? "—"
                        : `${scan.duration_seconds.toFixed(1)}s`}
                    </td>
                  </tr>
                ))}
            {!scansQuery.isLoading && items.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  className="px-6 py-12 text-center text-sm text-muted-foreground"
                  data-testid="admin-scans-empty"
                >
                  {t("admin.scans.empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <footer
        className="flex shrink-0 items-center justify-between border-t bg-card px-6 py-2 text-xs"
        data-testid="admin-scans-pagination"
      >
        <div className="flex items-center gap-2">
          <label
            htmlFor="admin-scans-page-size"
            className="text-muted-foreground"
          >
            {t("admin.users.pagination.page_size_label")}
          </label>
          <select
            id="admin-scans-page-size"
            data-testid="admin-scans-page-size"
            className="h-8 rounded-md border border-input bg-background px-2"
            value={pageSize}
            onChange={(e) => {
              setPageSize(
                Number(e.target.value) as (typeof PAGE_SIZE_OPTIONS)[number],
              );
            }}
          >
            {PAGE_SIZE_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">
            {t("admin.users.pagination.page_label", {
              page,
              total: totalPages,
            })}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            data-testid="admin-scans-page-prev"
          >
            {t("admin.users.pagination.previous")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            data-testid="admin-scans-page-next"
          >
            {t("admin.users.pagination.next")}
          </Button>
        </div>
      </footer>

      <AdminScanDrawer
        open={openScan !== null}
        scan={openScan}
        onOpenChange={(open) => {
          if (!open) setOpenScanId(null);
        }}
      />
    </div>
  );
}
