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
 * The "Export CSV" button runs an authenticated fetch + blob download so
 * the bearer token stays in the Authorization header (out of URL / history).
 *
 * PII columns (email / full_name) are sha256-fingerprinted at write time
 * (chore PR #8 F4) — the toolbar surfaces a hint that plain-text search
 * will not match those columns.
 */
import { Download, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

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

export function AdminAuditPage() {
  const { t } = useTranslation("admin");

  const [actorInput, setActorInput] = useState("");
  const [targetTable, setTargetTable] = useState<AuditTargetTable | "all">(
    "all",
  );
  const [actionInput, setActionInput] = useState("");
  const [fromInput, setFromInput] = useState("");
  const [toInput, setToInput] = useState("");
  const [qInput, setQInput] = useState("");
  const [qDebounced, setQDebounced] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] =
    useState<(typeof PAGE_SIZE_OPTIONS)[number]>(50);
  const [openEntry, setOpenEntry] = useState<AuditLogItem | null>(null);
  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  const toastSeq = useRef(0);
  const [exporting, setExporting] = useState(false);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setQDebounced(qInput);
      setPage(1);
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [qInput]);

  const queryParams = useMemo(
    () => ({
      actor_user_id: actorInput.trim() || null,
      target_table: targetTable === "all" ? null : targetTable,
      action: actionInput.trim() || null,
      from: fromInput || null,
      to: toInput || null,
      q: qDebounced.trim() || null,
      page,
      page_size: pageSize,
    }),
    [
      actorInput,
      targetTable,
      actionInput,
      fromInput,
      toInput,
      qDebounced,
      page,
      pageSize,
    ],
  );

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
            onChange={(e) => {
              setActorInput(e.target.value);
              setPage(1);
            }}
            className="h-9 font-mono text-xs"
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
            onChange={(e) => {
              setTargetTable(e.target.value as AuditTargetTable | "all");
              setPage(1);
            }}
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
            onChange={(e) => {
              setActionInput(e.target.value);
              setPage(1);
            }}
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
            value={fromInput}
            onChange={(e) => {
              setFromInput(e.target.value);
              setPage(1);
            }}
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
            value={toInput}
            onChange={(e) => {
              setToInput(e.target.value);
              setPage(1);
            }}
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
                    className="cursor-pointer border-b transition-colors hover:bg-accent/40 focus-within:bg-accent/40"
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
            onChange={(e) => {
              setPageSize(
                Number(e.target.value) as (typeof PAGE_SIZE_OPTIONS)[number],
              );
              setPage(1);
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
            data-testid="admin-audit-page-prev"
          >
            {t("admin.users.pagination.previous")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
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
