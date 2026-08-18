// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
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
import { ClipboardCheck, FilterX } from "lucide-react";
import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApprovalsDrawer } from "@/features/approvals/ApprovalsDrawer";
import { TransitionApprovalsPanel } from "@/features/approvals/TransitionApprovalsPanel";
import { useToast } from "@/components/ui/toast";
import { useApprovals } from "@/features/approvals/useApprovals";
import RelativeTime from "@/components/RelativeTime";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";
import type { ApprovalStatus } from "@/lib/approvalsApi";

// ---------------------------------------------------------------------------
// Status filter options
// ---------------------------------------------------------------------------

// M-13 — "open" is a UI compound filter (= pending + under_review). It is the
// DEFAULT view so already-disposed rows (approved / rejected) don't clutter
// the queue; the guide promises the default queue shows only open requests.
type StatusFilter = ApprovalStatus | "all" | "open";

const STATUS_OPTIONS: StatusFilter[] = [
  "open",
  "all",
  "pending",
  "under_review",
  "approved",
  "rejected",
];

/** API value for the "open" compound filter — comma list the BE expands to IN(...). */
const OPEN_STATUSES = "pending,under_review";

// W12 — URL filter parsers (filter URL persistence consistency). Reject any
// value not in the allowed unions so a stale or hand-edited URL doesn't poison
// the typed state. ISO 8601 (YYYY-MM-DD) is the only date shape we render, so
// reject anything else for from/to dates.
// M-13 — param absent (or unrecognised) now defaults to "open", not "all".
// An explicit ?status=all still shows every row.
function parseStatusFilter(v: string | null): StatusFilter {
  return v && (STATUS_OPTIONS as readonly string[]).includes(v)
    ? (v as StatusFilter)
    : "open";
}
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
function parseIsoDateParam(v: string | null): string {
  return v && ISO_DATE_RE.test(v) ? v : "";
}
// B2 - `?project=` goes into the list request, so a malformed value would
// take the whole page down with a 422 from the query-parameter validator.
// Checking the shape here turns that into a filter the page simply ignores.
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function parseUuidParam(v: string | null): string | null {
  return v && UUID_RE.test(v) ? v : null;
}

// `?approval=` is different: it addresses one drawer, and the drawer's own
// fetch already answers a bad id with a not-found. Rejecting the shape here
// would swallow the address silently instead, leaving a user who followed a
// stale link staring at the queue with no idea their link was dead. Length is
// bounded so a pathological URL cannot be forwarded as a request path.
function parseDrawerParam(v: string | null): string | null {
  const trimmed = v?.trim() ?? "";
  return trimmed && trimmed.length <= 100 ? trimmed : null;
}
function parsePageParam(v: string | null): number {
  if (!v) return 1;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) && n >= 1 ? n : 1;
}

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
    rejected: "border-status-danger-border bg-status-danger-subtle text-status-danger-foreground",
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

  // --- filter state (W12 — URL-derived so reload / share / back button
  //     restore the exact view). Defaults DELETE the param so the URL stays
  //     clean. Every filter change resets ?page so the user doesn't land on
  //     a now-empty page 4 after narrowing the result set. ---
  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = parseStatusFilter(searchParams.get("status"));
  const fromDt = parseIsoDateParam(searchParams.get("from"));
  const toDt = parseIsoDateParam(searchParams.get("to"));
  const page = parsePageParam(searchParams.get("page"));
  // B2 - the project filter the governance band and the dashboard tile link
  // to. Rejected unless it looks like a UUID, on the same principle as the
  // other parsers here: a hand-edited value should fall back to the whole
  // queue rather than be forwarded to the backend.
  const projectId = parseUuidParam(searchParams.get("project"));

  const updateFilterParam = useCallback(
    (key: string, next: string | null) => {
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          if (next == null || next === "") out.delete(key);
          else out.set(key, next);
          // Any filter change rewinds to page 1 — see comment above.
          out.delete("page");
          return out;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );
  // M-13 — the default value is now "open", so THAT is the one we delete from
  // the URL to keep it clean; "all" (and every concrete status) is persisted.
  const setStatusFilter = useCallback(
    (next: StatusFilter) => updateFilterParam("status", next === "open" ? null : next),
    [updateFilterParam],
  );
  const setFromDt = useCallback(
    (next: string) => updateFilterParam("from", next || null),
    [updateFilterParam],
  );
  const setToDt = useCallback(
    (next: string) => updateFilterParam("to", next || null),
    [updateFilterParam],
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

  // B2 - the drawer lives in the URL, not in component state. Opening a row
  // used to leave the address bar untouched, so the view could not be shared
  // or reloaded and Back left the page entirely instead of closing the panel.
  // `replace: false` is the point: the open drawer is its own history entry,
  // which is what makes Back close it. Same treatment the vulnerability,
  // compliance and obligation drawers got in A2 and A3.
  const openId = parseDrawerParam(searchParams.get("approval"));
  const setOpenId = useCallback(
    (next: string | null) => {
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          if (next === null) out.delete("approval");
          else out.set("approval", next);
          return out;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );

  // --- toast ---
  const { toast } = useToast();

  function notify(text: string, tone: "success" | "error", key?: string) {
    toast(text, { tone, key });
  }

  const queryParams = useMemo(
    () => ({
      status:
        statusFilter === "all"
          ? null
          : statusFilter === "open"
            ? OPEN_STATUSES
            : statusFilter,
      project_id: projectId,
      from_dt: fromDt || null,
      to_dt: toDt || null,
      page,
      page_size: PAGE_SIZE,
    }),
    [statusFilter, projectId, fromDt, toDt, page],
  );

  const approvalsQuery = useApprovals(queryParams);
  const items = approvalsQuery.data?.items ?? [];
  const total = approvalsQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // B2 - anything that can make a non-empty queue look empty counts, `page`
  // included: landing on page 3 of a queue that has shrunk shows no rows and
  // is not the same thing as having none. "open" is the default view rather
  // than a choice the user made, so it does not count.
  const hasFilters =
    statusFilter !== "open" ||
    projectId !== null ||
    fromDt !== "" ||
    toDt !== "" ||
    page > 1;
  // The signed-in user, so their own requests read as waiting rather than as
  // something they can wave through themselves.
  const currentUserId = useAuthStore((s) => s.user?.id ?? null);
  const clearFilters = useCallback(() => {
    setSearchParams(new URLSearchParams(), { replace: false });
  }, [setSearchParams]);

  // The name of the project the queue is scoped to, taken from the rows the
  // filter returned. There is no separate lookup for it: one more request to
  // label a chip is not worth it, and the rows already carry the name.
  //
  // Two things the first version got wrong. The comparison was
  // case-sensitive while Postgres compares uuids case-insensitively, so an
  // uppercase `?project=` filtered correctly and then failed to find its own
  // rows. And when no row supplied a name it printed eight characters of the
  // id, which is the thing this unit set out to stop showing people.
  const projectFilterLabel = projectId
    ? (items.find(
        (i) => i.project_id.toLowerCase() === projectId.toLowerCase(),
      )?.project_name ?? t("approvals.filter.project_unnamed"))
    : null;

  return (
    <div
      className="flex h-full flex-col"
      data-testid="approvals-page"
    >
      {/* Page header */}
      <PageHeader
        title={t("approvals.title")}
        description={t("approvals.subtitle")}
      />

      {/* Status changes waiting on a second person. Renders nothing when the
          queue is empty, which is every deployment that has not turned the
          policy on. */}
      <div className="px-6 pt-4 empty:hidden">
        <TransitionApprovalsPanel currentUserId={currentUserId} />
      </div>

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
            }}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt === "all"
                  ? t("approvals.filter.status_all")
                  : opt === "open"
                    ? t("approvals.filter.status_open")
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
            }}
          />
        </div>

        {/* B2 - when the queue arrives scoped to one project, say so and
            offer the way out. Without this the user sees a short queue and
            no reason for it, and the governance band that sent them here is
            off-screen. The chip is not a control for choosing a project:
            picking one belongs on the project page, and a select listing
            every project the actor can see would be a second inventory. */}
        {projectId ? (
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">
              {t("approvals.column.project")}
            </span>
            <div className="flex h-8 items-center gap-2 rounded-md border border-input bg-background px-2">
              <span
                className="max-w-[16rem] truncate text-sm"
                data-testid="approvals-project-filter"
                title={projectId}
              >
                {projectFilterLabel}
              </span>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-1 text-xs"
                onClick={() => updateFilterParam("project", null)}
                data-testid="approvals-project-filter-clear"
                aria-label={t("approvals.filter.clear_project")}
              >
                {t("approvals.filter.clear_project")}
              </Button>
            </div>
          </div>
        ) : null}

        {/* The size of what is being looked at. The pagination footer said
            "1 / 4" and nothing said how many rows that was. Absent while the
            request is in flight or after it failed: "0 requests" would be a
            count nobody counted. */}
        {approvalsQuery.isSuccess ? (
          <div className="ml-auto flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">&nbsp;</span>
            <span
              className="flex h-8 items-center text-sm text-muted-foreground"
              data-testid="approvals-total"
            >
              {t("approvals.total", { count: total })}
            </span>
          </div>
        ) : null}

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

                    {/* B2 - the requester by name. This column used to print
                        the first eight characters of a uuid, which names
                        nobody and cannot be chased up. The backend resolves
                        it the same way the project list resolves its creator
                        column.

                        The id branch is defensive rather than reachable from
                        the list: the approvals table sets the requester to
                        NULL when the user row goes (see the 0008 migration),
                        so a missing name comes with a missing id. It stays
                        because the single-row endpoints are separate code
                        paths, and it costs one line. */}
                    <td className="px-3">
                      {item.requested_by_name ? (
                        <span className="text-sm text-foreground">
                          {item.requested_by_name}
                        </span>
                      ) : item.requested_by_user_id ? (
                        <span
                          className="font-mono text-xs text-muted-foreground"
                          title={item.requested_by_user_id}
                        >
                          {item.requested_by_user_id.slice(0, 8)}
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          {t("approvals.requested_by_unknown")}
                        </span>
                      )}
                    </td>

                    {/* Requested at */}
                    <td className="px-3 text-xs text-muted-foreground">
                      <RelativeTime
                        value={item.requested_at}
                        locale={i18n.resolvedLanguage}
                      />
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

            {/* B2 - two states, not one sentence. "No approvals match the
                current filters" is a lie on an empty queue with no filters
                set, and it hides the good news. The shared EmptyState is what
                every other zero-state in the product uses.

                Neither sentence is shown when the request failed. A query
                that did not answer is not a queue with nothing in it, and
                saying so beside a red error banner (and beside a total of
                zero) states a fact nobody established. */}
            {!approvalsQuery.isLoading &&
            !approvalsQuery.isError &&
            items.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-6 py-6">
                  <EmptyState
                    data-testid="approvals-empty"
                    icon={hasFilters ? <FilterX /> : <ClipboardCheck />}
                    title={
                      hasFilters
                        ? t("approvals.empty_filtered")
                        : t("approvals.empty_none")
                    }
                    description={
                      hasFilters
                        ? t("approvals.empty_filtered_help")
                        : t("approvals.empty_none_help")
                    }
                    action={
                      hasFilters ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={clearFilters}
                          data-testid="approvals-clear-filters"
                        >
                          {t("approvals.filter.clear")}
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

      {/* Pagination */}
      <footer
        className="flex shrink-0 items-center justify-between border-t bg-card px-6 py-2 text-xs"
        data-testid="approvals-pagination"
      >
        <span className="text-muted-foreground" data-testid="approvals-page-label">
          {t("approvals.pagination.summary", { page, total: totalPages })}
        </span>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage(Math.max(1, page - 1))}
            data-testid="approvals-page-prev"
          >
            {t("approvals.action.previous")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage(Math.min(totalPages, page + 1))}
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

    </div>
  );
}
