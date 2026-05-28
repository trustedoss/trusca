/**
 * ApprovalsPage — Phase 4 PR #15.
 *
 * Compact 40px-row table showing the component approval queue. Inline filters
 * (status, date range) live at the top — no modal dialogs. Clicking a row or
 * the Actions button opens ApprovalsDrawer from the right.
 *
 * Design tokens used:
 *   - var(--table-row) for 40px compact row height.
 *   - Status colors via Tailwind classes (yellow / blue / green / red) — not
 *     hex literals — to satisfy CLAUDE.md "never hardcode color hex values".
 *   - Color is paired with a text label (CLAUDE.md accessibility rule).
 */
import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApprovalsDrawer } from "@/features/approvals/ApprovalsDrawer";
import { AdminToast, type AdminToastMessage } from "@/features/admin/components/AdminToast";
import { useApprovals } from "@/features/approvals/useApprovals";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";
import type { ApprovalStatus } from "@/lib/approvalsApi";

// ---------------------------------------------------------------------------
// Status filter options
// ---------------------------------------------------------------------------

type StatusFilter = ApprovalStatus | "all";

const STATUS_OPTIONS: StatusFilter[] = [
  "all",
  "pending",
  "under_review",
  "approved",
  "rejected",
];

// ---------------------------------------------------------------------------
// Status badge — inline to avoid a cross-feature import
// ---------------------------------------------------------------------------

function StatusBadge({
  status,
  t,
}: {
  status: ApprovalStatus;
  t: (key: string) => string;
}) {
  const colorMap: Record<ApprovalStatus, string> = {
    pending: "border-yellow-300 bg-yellow-50 text-yellow-700",
    under_review: "border-blue-300 bg-blue-50 text-blue-700",
    approved: "border-green-300 bg-green-50 text-green-700",
    rejected: "border-red-300 bg-red-50 text-red-700",
  };
  return (
    <Badge
      variant="outline"
      className={cn(colorMap[status])}
      data-status={status}
    >
      {t(`approvals.status.${status}`)}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const PAGE_SIZE = 25;

export function ApprovalsPage() {
  const { t, i18n } = useTranslation("approvals");

  // --- filter state ---
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [fromDt, setFromDt] = useState("");
  const [toDt, setToDt] = useState("");
  const [page, setPage] = useState(1);

  // --- drawer state ---
  const [openId, setOpenId] = useState<string | null>(null);

  // --- toast ---
  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  const toastSeq = useRef(0);

  function notify(text: string, tone: "success" | "error", key?: string) {
    toastSeq.current += 1;
    setToast({ id: toastSeq.current, text, tone, key });
  }

  const queryParams = useMemo(
    () => ({
      status: statusFilter === "all" ? null : statusFilter,
      from_dt: fromDt || null,
      to_dt: toDt || null,
      page,
      page_size: PAGE_SIZE,
    }),
    [statusFilter, fromDt, toDt, page],
  );

  const approvalsQuery = useApprovals(queryParams);
  const items = approvalsQuery.data?.items ?? [];
  const total = approvalsQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div
      className="flex h-full flex-col"
      data-testid="approvals-page"
    >
      {/* Page header */}
      <header className="border-b bg-card px-6 py-4">
        <h1 className="text-lg font-semibold tracking-tight">
          {t("approvals.title")}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("approvals.subtitle")}
        </p>
      </header>

      {/* Inline filters toolbar */}
      <div className="flex flex-wrap items-end gap-3 border-b bg-card px-6 py-3">
        {/* Status filter */}
        <div className="flex flex-col gap-1">
          <Label
            htmlFor="approval-status-filter"
            className="text-xs text-muted-foreground"
          >
            {t("approvals.column.status")}
          </Label>
          <select
            id="approval-status-filter"
            data-testid="approval-status-filter"
            className="h-8 rounded-md border border-input bg-background px-2 text-sm"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value as StatusFilter);
              setPage(1);
            }}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt === "all"
                  ? t("approvals.filter.status_all")
                  : t(`approvals.status.${opt}`)}
              </option>
            ))}
          </select>
        </div>

        {/* P2 #7 — From / To date pickers.
            Native `<input type="date">` renders its calendar popover in the
            OS locale (a Korean macOS shows "2026년 5월 26일"). We do NOT
            want to ship a 50 kB react-day-picker just for this control, so
            instead:
              1. `lang="en"` forces Chrome / Edge to render an English
                 datepicker regardless of OS locale (Safari falls back to
                 OS, Firefox ignores `lang` on `input[type=date]` — both
                 are then covered by step 2).
              2. The visible value is YYYY-MM-DD (ISO 8601) which is
                 locale-agnostic, so even on a fallback UI the input value
                 itself stays English.
              3. The placeholder makes the expected format explicit. */}
        <div className="flex flex-col gap-1">
          <Label
            htmlFor="approval-from-dt"
            className="text-xs text-muted-foreground"
          >
            {t("approvals.filter.from_label")}
          </Label>
          <Input
            id="approval-from-dt"
            data-testid="approval-from-dt"
            type="date"
            lang="en"
            placeholder="YYYY-MM-DD"
            className="h-8 w-36 text-sm"
            value={fromDt}
            onChange={(e) => {
              setFromDt(e.target.value);
              setPage(1);
            }}
          />
        </div>

        {/* To date */}
        <div className="flex flex-col gap-1">
          <Label
            htmlFor="approval-to-dt"
            className="text-xs text-muted-foreground"
          >
            {t("approvals.filter.to_label")}
          </Label>
          <Input
            id="approval-to-dt"
            data-testid="approval-to-dt"
            type="date"
            lang="en"
            placeholder="YYYY-MM-DD"
            className="h-8 w-36 text-sm"
            value={toDt}
            onChange={(e) => {
              setToDt(e.target.value);
              setPage(1);
            }}
          />
        </div>

        {/* Refresh */}
        <Button
          size="sm"
          variant="outline"
          onClick={() => void approvalsQuery.refetch()}
          data-testid="approvals-refresh"
        >
          {t("approvals.action.refresh")}
        </Button>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto">
        {approvalsQuery.isError ? (
          <div className="px-6 py-4">
            <Alert variant="destructive" data-testid="approvals-error">
              <AlertDescription>
                {t("approvals.errors.unknown")}
              </AlertDescription>
            </Alert>
          </div>
        ) : null}

        <table
          className="w-full text-sm"
          data-testid="approvals-table"
          aria-busy={approvalsQuery.isLoading}
        >
          <thead className="sticky top-0 bg-card">
            <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-6 py-2">
                {t("approvals.column.component")}
              </th>
              <th className="px-3 py-2">
                {t("approvals.column.project")}
              </th>
              <th className="px-3 py-2">
                {t("approvals.column.status")}
              </th>
              <th className="px-3 py-2">
                {t("approvals.column.requested_by")}
              </th>
              <th className="px-3 py-2">
                {t("approvals.column.requested_at")}
              </th>
              <th className="px-3 py-2 text-right">
                {t("approvals.column.actions")}
              </th>
            </tr>
          </thead>
          <tbody data-testid="approvals-tbody">
            {approvalsQuery.isLoading
              ? Array.from({ length: 6 }).map((_, i) => (
                  <tr key={`skeleton-${i}`} className="border-b">
                    <td className="px-6 py-2" colSpan={6}>
                      <Skeleton className="h-5 w-full" />
                    </td>
                  </tr>
                ))
              : items.map((item) => (
                  <tr
                    key={item.id}
                    data-testid="approvals-row"
                    data-approval-id={item.id}
                    data-status={item.status}
                    className={cn(
                      "cursor-pointer border-b transition-colors duration-fast ease-out-soft hover:bg-accent/40 focus-within:bg-accent/40",
                    )}
                    style={{ height: "var(--table-row)" }}
                    tabIndex={0}
                    onClick={() => setOpenId(item.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setOpenId(item.id);
                      }
                    }}
                  >
                    {/* P1 #6 — Component column: show the component name with
                        purl in a smaller line below; fall back to the legacy
                        UUID prefix when the BE didn't supply the labels (e.g.
                        a single-row endpoint or a hard-deleted referent). */}
                    <td className="px-6">
                      {item.component_name ? (
                        <div className="flex flex-col">
                          <span className="text-sm font-medium text-foreground">
                            {item.component_name}
                          </span>
                          {item.component_purl ? (
                            <span
                              className="truncate font-mono text-xs text-muted-foreground"
                              title={item.component_purl}
                            >
                              {item.component_purl}
                            </span>
                          ) : null}
                        </div>
                      ) : (
                        <span className="font-mono text-xs">
                          {item.component_id.slice(0, 8)}
                        </span>
                      )}
                    </td>

                    {/* P1 #6 — Project column: link to /projects/{id} when
                        BE surfaced the name. Stop click propagation so the
                        link doesn't also trip the row's drawer-open handler. */}
                    <td className="px-3">
                      {item.project_name ? (
                        <a
                          href={`/projects/${item.project_id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="text-sm font-medium text-foreground hover:underline"
                          data-testid="approvals-row-project-link"
                        >
                          {item.project_name}
                        </a>
                      ) : (
                        <span className="font-mono text-xs">
                          {item.project_id.slice(0, 8)}
                        </span>
                      )}
                    </td>

                    {/* Status */}
                    <td className="px-3">
                      <StatusBadge status={item.status} t={t} />
                    </td>

                    {/* Requested by */}
                    <td className="px-3">
                      <span className="font-mono text-xs text-muted-foreground">
                        {item.requested_by_user_id
                          ? item.requested_by_user_id.slice(0, 8)
                          : "—"}
                      </span>
                    </td>

                    {/* Requested at */}
                    <td className="px-3 text-xs text-muted-foreground">
                      {formatRelativeToNow(
                        item.requested_at,
                        i18n.resolvedLanguage,
                      )}
                    </td>

                    {/* Actions */}
                    <td className="px-3 text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenId(item.id);
                        }}
                        data-testid="approvals-row-action"
                        aria-label={t("approvals.column.actions")}
                      >
                        {t("approvals.column.actions")}
                      </Button>
                    </td>
                  </tr>
                ))}

            {!approvalsQuery.isLoading && items.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-6 py-12 text-center text-sm text-muted-foreground"
                  data-testid="approvals-empty"
                >
                  {t("approvals.empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <footer
        className="flex shrink-0 items-center justify-between border-t bg-card px-6 py-2 text-xs"
        data-testid="approvals-pagination"
      >
        <span className="text-muted-foreground">
          {/* e.g., "Page 1 of 4" — use ICU via count-aware key */}
          {`${page} / ${totalPages}`}
        </span>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            data-testid="approvals-page-prev"
          >
            {t("approvals.action.previous")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            data-testid="approvals-page-next"
          >
            {t("approvals.action.next")}
          </Button>
        </div>
      </footer>

      <ApprovalsDrawer
        open={openId !== null}
        approvalId={openId}
        onOpenChange={(open) => {
          if (!open) setOpenId(null);
        }}
        notify={notify}
      />

      <AdminToast message={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
