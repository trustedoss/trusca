/**
 * AdminAuditPage — Phase 4 PR #14 §4.7.
 *
 * Inline filter toolbar (no modal) over a compact 40px-row table. Filters:
 *   - actor_user_id     — UUID free-text input.
 *   - target_table      — closed-enum select (matches AuditTargetTable).
 *   - action            — free-text input (max 64 chars, validated server side).
 *   - from / to         — datetime-local inputs.
 *   - q                 — diff substring search (300ms debounce).
 *
 * L-14: filter state lives in the URL (`useSearchParams`, ApprovalsPage
 * pattern) so a filtered view survives reload and can be shared. Params:
 * actor / target_table / action / from / to / q / page / page_size.
 * Defaults DELETE the param so the URL stays clean; unknown or invalid
 * values fall back to the default. Text inputs keep a local mirror and
 * commit to the URL after a 300ms debounce so typing doesn't spam history.
 *
 * L-15: the list auto-refreshes every 2.5s (see useAdminAudit) so writes
 * show up without pressing Refresh.
 *
 * The "Export CSV" button runs an authenticated fetch + blob download so
 * the bearer token stays in the Authorization header (out of URL / history).
 *
 * PII columns (email / full_name) are sha256-fingerprinted at write time
 * (chore PR #8 F4) — the toolbar surfaces a hint that plain-text search
 * will not match those columns.
 */
import { Download, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { AdminAuditDrawer } from "@/features/admin/audit/AdminAuditDrawer";
import {
  AUDIT_TARGET_TABLES,
  downloadAdminAuditCsv,
  type AuditLogItem,
  type AuditTargetTable,
} from "@/features/admin/audit/api/adminAuditApi";
import { useAdminAudit } from "@/features/admin/audit/api/useAdminAudit";
import {
  AdminToast,
  type AdminToastMessage,
} from "@/features/admin/components/AdminToast";
import {
  adminErrorExtension,
  adminErrorMessageKey,
} from "@/features/admin/lib/adminErrorMessage";
import { cn } from "@/lib/utils";

const PAGE_SIZE_OPTIONS = [25, 50, 100] as const;
const DEFAULT_PAGE_SIZE = 50;

// L-14 — URL filter parsers (ApprovalsPage pattern). Reject any value
// outside the allowed shape so a stale or hand-edited URL doesn't poison
// the typed state — invalid values fall back to the default.
function parseTargetTableParam(v: string | null): AuditTargetTable | "all" {
  return v && (AUDIT_TARGET_TABLES as readonly string[]).includes(v)
    ? (v as AuditTargetTable)
    : "all";
}
// datetime-local values — YYYY-MM-DDTHH:mm with optional :ss.
const DATETIME_LOCAL_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/;
function parseDateTimeParam(v: string | null): string {
  return v && DATETIME_LOCAL_RE.test(v) ? v : "";
}
function parseTextParam(v: string | null, maxLength: number): string {
  return v ? v.slice(0, maxLength) : "";
}
function parsePageParam(v: string | null): number {
  if (!v) return 1;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) && n >= 1 ? n : 1;
}
function parsePageSizeParam(
  v: string | null,
): (typeof PAGE_SIZE_OPTIONS)[number] {
  const n = v ? Number.parseInt(v, 10) : Number.NaN;
  return (PAGE_SIZE_OPTIONS as readonly number[]).includes(n)
    ? (n as (typeof PAGE_SIZE_OPTIONS)[number])
    : DEFAULT_PAGE_SIZE;
}

/**
 * Local mirror for a URL-backed text filter. The input updates on every
 * keystroke; the URL param updates after 300ms of quiet so typing doesn't
 * create a history entry (and a server query) per character. A param
 * change from outside (back/forward, hand-edited URL) flows back into the
 * input. Committing the raw value keeps `paramValue === input` after the
 * round-trip so the debounce effect settles.
 */
function useDebouncedFilterInput(
  paramValue: string,
  commit: (next: string | null) => void,
): [string, (next: string) => void] {
  const [input, setInput] = useState(paramValue);
  useEffect(() => {
    setInput(paramValue);
  }, [paramValue]);
  useEffect(() => {
    if (input === paramValue) return undefined;
    const id = setTimeout(() => commit(input === "" ? null : input), 300);
    return () => clearTimeout(id);
  }, [input, paramValue, commit]);
  return [input, setInput];
}

export function AdminAuditPage() {
  const { t } = useTranslation("admin");

  // --- filter state — URL-derived (L-14). searchParams is the single
  //     source; the three text inputs keep a debounced local mirror. ---
  const [searchParams, setSearchParams] = useSearchParams();
  const actorParam = parseTextParam(searchParams.get("actor"), 255);
  const targetTable = parseTargetTableParam(searchParams.get("target_table"));
  const actionParam = parseTextParam(searchParams.get("action"), 64);
  const fromParam = parseDateTimeParam(searchParams.get("from"));
  const toParam = parseDateTimeParam(searchParams.get("to"));
  const qParam = parseTextParam(searchParams.get("q"), 255);
  const page = parsePageParam(searchParams.get("page"));
  const pageSize = parsePageSizeParam(searchParams.get("page_size"));

  const updateFilterParam = useCallback(
    (key: string, next: string | null) => {
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          if (next == null || next === "") out.delete(key);
          else out.set(key, next);
          // Any filter change rewinds to page 1 so the user doesn't land
          // on a now-empty page after narrowing the result set.
          out.delete("page");
          return out;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );
  const setPage = useCallback(
    (next: number) => {
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          if (next <= 1) out.delete("page");
          else out.set("page", String(next));
          return out;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );
  const setPageSize = useCallback(
    (next: number) =>
      updateFilterParam(
        "page_size",
        next === DEFAULT_PAGE_SIZE ? null : String(next),
      ),
    [updateFilterParam],
  );

  const commitActor = useCallback(
    (next: string | null) => updateFilterParam("actor", next),
    [updateFilterParam],
  );
  const [actorInput, setActorInput] = useDebouncedFilterInput(
    actorParam,
    commitActor,
  );
  const commitAction = useCallback(
    (next: string | null) => updateFilterParam("action", next),
    [updateFilterParam],
  );
  const [actionInput, setActionInput] = useDebouncedFilterInput(
    actionParam,
    commitAction,
  );
  const commitQ = useCallback(
    (next: string | null) => updateFilterParam("q", next),
    [updateFilterParam],
  );
  const [qInput, setQInput] = useDebouncedFilterInput(qParam, commitQ);

  const [openEntry, setOpenEntry] = useState<AuditLogItem | null>(null);
  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  const toastSeq = useRef(0);
  const [exporting, setExporting] = useState(false);

  const queryParams = useMemo(
    () => ({
      actor_user_id: actorParam.trim() || null,
      target_table: targetTable === "all" ? null : targetTable,
      action: actionParam.trim() || null,
      from: fromParam || null,
      to: toParam || null,
      q: qParam.trim() || null,
      page,
      page_size: pageSize,
    }),
    [
      actorParam,
      targetTable,
      actionParam,
      fromParam,
      toParam,
      qParam,
      page,
      pageSize,
    ],
  );

  // L-15 — the 2.5s polling stays active while the drawer is open: the
  // drawer renders from the `openEntry` snapshot captured at click time
  // (it never re-derives from the refetched list), so replacing the row
  // objects underneath cannot shake its content.
  const auditQuery = useAdminAudit(queryParams);
  const items = auditQuery.data?.items ?? [];
  const total = auditQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  function notify(text: string, tone: "success" | "error", key?: string) {
    toastSeq.current += 1;
    setToast({ id: toastSeq.current, text, tone, key });
  }

  async function handleExport() {
    setExporting(true);
    try {
      const { blobUrl, filename } = await downloadAdminAuditCsv({
        actor_user_id: queryParams.actor_user_id,
        target_table: queryParams.target_table,
        action: queryParams.action,
        from: queryParams.from,
        to: queryParams.to,
        q: queryParams.q,
      });
      // Programmatic anchor click — keeps the bearer header path; the
      // browser drives the download dialog from the blob.
      const anchor = document.createElement("a");
      anchor.href = blobUrl;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      // Free the blob URL after the navigation has been queued. setTimeout
      // gives the browser a tick to start the download before we revoke.
      setTimeout(() => URL.revokeObjectURL(blobUrl), 4000);
      notify(t("admin.audit.toast.csv_started"), "success", "csv_started");
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="flex h-full flex-col" data-testid="admin-audit-page">
      <header className="border-b bg-card px-6 py-4">
        <h1 className="text-lg font-semibold tracking-tight">
          {t("admin.audit.title")}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("admin.audit.subtitle")}
        </p>
      </header>

      <div
        className="grid grid-cols-1 gap-3 border-b bg-card px-6 py-3 sm:grid-cols-2 lg:grid-cols-6"
        data-testid="admin-audit-toolbar"
      >
        <div>
          <Label
            htmlFor="admin-audit-actor"
            className="text-xs text-muted-foreground"
          >
            {t("admin.audit.filter.actor_user_id")}
          </Label>
          <Input
            id="admin-audit-actor"
            data-testid="admin-audit-actor"
            value={actorInput}
            onChange={(e) => setActorInput(e.target.value)}
            className="h-9 font-mono text-xs"
            maxLength={255}
          />
        </div>
        <div>
          <Label
            htmlFor="admin-audit-target-table"
            className="text-xs text-muted-foreground"
          >
            {t("admin.audit.filter.target_table_label")}
          </Label>
          <select
            id="admin-audit-target-table"
            data-testid="admin-audit-target-table"
            className={cn(
              "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
            )}
            value={targetTable}
            onChange={(e) =>
              updateFilterParam(
                "target_table",
                e.target.value === "all" ? null : e.target.value,
              )
            }
          >
            <option value="all">
              {t("admin.audit.filter.target_table_all")}
            </option>
            {AUDIT_TARGET_TABLES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label
            htmlFor="admin-audit-action"
            className="text-xs text-muted-foreground"
          >
            {t("admin.audit.filter.action_label")}
          </Label>
          <Input
            id="admin-audit-action"
            data-testid="admin-audit-action"
            value={actionInput}
            placeholder={t("admin.audit.filter.action_placeholder")}
            onChange={(e) => setActionInput(e.target.value)}
            className="h-9 font-mono text-xs"
            maxLength={64}
          />
        </div>
        <div>
          <Label
            htmlFor="admin-audit-from"
            className="text-xs text-muted-foreground"
          >
            {t("admin.audit.filter.from_label")}
          </Label>
          <Input
            id="admin-audit-from"
            data-testid="admin-audit-from"
            type="datetime-local"
            value={fromParam}
            onChange={(e) => updateFilterParam("from", e.target.value || null)}
            className="h-9 font-mono text-xs"
          />
        </div>
        <div>
          <Label
            htmlFor="admin-audit-to"
            className="text-xs text-muted-foreground"
          >
            {t("admin.audit.filter.to_label")}
          </Label>
          <Input
            id="admin-audit-to"
            data-testid="admin-audit-to"
            type="datetime-local"
            value={toParam}
            onChange={(e) => updateFilterParam("to", e.target.value || null)}
            className="h-9 font-mono text-xs"
          />
        </div>
        <div>
          <Label
            htmlFor="admin-audit-q"
            className="text-xs text-muted-foreground"
          >
            {t("admin.audit.filter.q_label")}
          </Label>
          <Input
            id="admin-audit-q"
            data-testid="admin-audit-q"
            value={qInput}
            placeholder={t("admin.audit.filter.q_placeholder")}
            onChange={(e) => setQInput(e.target.value)}
            className="h-9"
            maxLength={255}
          />
        </div>
      </div>

      <div className="flex items-center justify-between gap-2 border-b bg-card px-6 py-2 text-xs text-muted-foreground">
        <span data-testid="admin-audit-pii-hint">
          {t("admin.audit.filter.q_pii_hint")}
        </span>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => auditQuery.refetch()}
            disabled={auditQuery.isFetching}
            data-testid="admin-audit-refresh"
          >
            <RefreshCw
              className={cn(
                "h-4 w-4",
                auditQuery.isFetching && "animate-spin",
              )}
              aria-hidden
            />
            {t("admin.audit.actions.refresh")}
          </Button>
          <Button
            size="sm"
            onClick={handleExport}
            disabled={exporting}
            data-testid="admin-audit-export-csv"
          >
            <Download className="h-4 w-4" aria-hidden />
            {t("admin.audit.actions.export_csv")}
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {auditQuery.isError ? (
          <div className="px-6 py-4">
            <Alert variant="destructive" data-testid="admin-audit-error">
              <AlertDescription>{t("admin.errors.unknown")}</AlertDescription>
            </Alert>
          </div>
        ) : null}

        <table
          className="w-full text-sm"
          data-testid="admin-audit-table"
          aria-busy={auditQuery.isLoading}
        >
          <thead className="sticky top-0 bg-card">
            <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-6 py-2">{t("admin.audit.column.created_at")}</th>
              <th className="px-3 py-2">{t("admin.audit.column.actor")}</th>
              <th className="px-3 py-2">{t("admin.audit.column.target_table")}</th>
              <th className="px-3 py-2">{t("admin.audit.column.action")}</th>
              <th className="px-3 py-2">{t("admin.audit.column.target_id")}</th>
              <th className="px-3 py-2">{t("admin.audit.column.request_id")}</th>
            </tr>
          </thead>
          <tbody data-testid="admin-audit-tbody">
            {auditQuery.isLoading
              ? Array.from({ length: 6 }).map((_, i) => (
                  <tr key={`skeleton-${i}`} className="border-b">
                    <td className="px-6 py-2" colSpan={6}>
                      <Skeleton className="h-5 w-full" />
                    </td>
                  </tr>
                ))
              : items.map((entry) => (
                  <tr
                    key={entry.id}
                    data-testid="admin-audit-row"
                    data-row-id={entry.id}
                    data-target-table={entry.target_table}
                    data-action={entry.action}
                    className="cursor-pointer border-b transition-colors duration-fast ease-out-soft hover:bg-accent/40 focus-within:bg-accent/40"
                    style={{ height: "var(--table-row)" }}
                    tabIndex={0}
                    onClick={() => setOpenEntry(entry)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setOpenEntry(entry);
                      }
                    }}
                  >
                    <td className="px-6 font-mono text-[11px] text-muted-foreground">
                      {entry.created_at}
                    </td>
                    <td className="truncate px-3 font-mono text-xs">
                      {entry.actor_email ?? entry.actor_user_id ?? "—"}
                    </td>
                    <td className="px-3 font-mono text-xs">
                      {entry.target_table}
                    </td>
                    <td className="px-3 font-mono text-xs">{entry.action}</td>
                    <td className="truncate px-3 font-mono text-[11px] text-muted-foreground">
                      {entry.target_id ?? "—"}
                    </td>
                    <td className="truncate px-3 font-mono text-[11px] text-muted-foreground">
                      {entry.request_id ?? "—"}
                    </td>
                  </tr>
                ))}
            {!auditQuery.isLoading && items.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-6 py-12 text-center text-sm text-muted-foreground"
                  data-testid="admin-audit-empty"
                >
                  {t("admin.audit.empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <footer
        className="flex shrink-0 items-center justify-between border-t bg-card px-6 py-2 text-xs"
        data-testid="admin-audit-pagination"
      >
        <div className="flex items-center gap-2">
          <label
            htmlFor="admin-audit-page-size"
            className="text-muted-foreground"
          >
            {t("admin.users.pagination.page_size_label")}
          </label>
          <select
            id="admin-audit-page-size"
            data-testid="admin-audit-page-size"
            className="h-8 rounded-md border border-input bg-background px-2"
            value={pageSize}
            onChange={(e) => setPageSize(Number(e.target.value))}
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
            onClick={() => setPage(Math.max(1, page - 1))}
            data-testid="admin-audit-page-prev"
          >
            {t("admin.users.pagination.previous")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage(Math.min(totalPages, page + 1))}
            data-testid="admin-audit-page-next"
          >
            {t("admin.users.pagination.next")}
          </Button>
        </div>
      </footer>

      <AdminAuditDrawer
        open={openEntry !== null}
        entry={openEntry}
        onOpenChange={(open) => {
          if (!open) setOpenEntry(null);
        }}
      />

      <AdminToast message={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
