/**
 * AdminDTPage — Phase 4 PR #14 §4.4.
 *
 * Two-section page:
 *   - Top status card: breaker state badge (green=closed, yellow=half_open,
 *     red=open), consecutive fail count, DT version, last check time, last
 *     error. "Force health probe" button on the right.
 *   - Bottom orphan-projects card: compact 40px-row table with checkboxes,
 *     "Clean up selected" + "Clean up all" buttons. Both actions surface an
 *     inline confirm strip (PR #13 pattern) before firing the Celery task.
 *
 * No modal. Filters are inline. Color is paired with an icon + i18n label so
 * accessibility doesn't depend on hue alone (CLAUDE.md "Accessibility").
 *
 * Polling: status refetches every 30s via the query hook. The orphan list
 * is only fetched on demand (refresh button) since the catalog can be
 * large and the data is not time-critical.
 */
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { BreakerState } from "@/features/admin/dt/api/adminDTApi";
import {
  useCleanupDTOrphans,
  useDTOrphans,
  useDTStatus,
  useForceDTHealthCheck,
  useResetDTBreaker,
} from "@/features/admin/dt/api/useAdminDT";
import {
  AdminToast,
  type AdminToastMessage,
} from "@/features/admin/components/AdminToast";
import {
  adminErrorExtension,
  adminErrorMessageKey,
} from "@/features/admin/lib/adminErrorMessage";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;

type ConfirmKind = "cleanup_selected" | "cleanup_all" | "reset_breaker" | null;

function breakerTone(state: BreakerState): "ok" | "degraded" | "down" {
  if (state === "closed") return "ok";
  if (state === "half_open") return "degraded";
  return "down";
}

function BreakerBadge({ state }: { state: BreakerState }) {
  const { t } = useTranslation("admin");
  const tone = breakerTone(state);
  return (
    <Badge
      variant="outline"
      data-testid="dt-breaker-badge"
      data-state={state}
      data-tone={tone}
      className={cn(
        "gap-1 font-mono text-xs",
        tone === "ok" && "border-emerald-300 bg-emerald-50 text-emerald-700",
        tone === "degraded" && "border-amber-300 bg-amber-50 text-amber-800",
        tone === "down" && "border-red-300 bg-red-50 text-red-700",
      )}
    >
      {tone === "ok" ? (
        <CheckCircle2 className="h-3 w-3" aria-hidden />
      ) : tone === "degraded" ? (
        <ShieldAlert className="h-3 w-3" aria-hidden />
      ) : (
        <AlertCircle className="h-3 w-3" aria-hidden />
      )}
      {t(`admin.dt.breaker.${state}`)}
    </Badge>
  );
}

interface MetaRowProps {
  label: string;
  value: string;
  testId?: string;
}

function MetaRow({ label, value, testId }: MetaRowProps) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="font-mono text-xs" data-testid={testId}>
        {value}
      </span>
    </div>
  );
}

export function AdminDTPage() {
  const { t, i18n } = useTranslation("admin");

  const statusQuery = useDTStatus();
  const orphansQuery = useDTOrphans({ limit: PAGE_SIZE, offset: 0 });
  const cleanup = useCleanupDTOrphans();
  const probe = useForceDTHealthCheck();
  const resetBreaker = useResetDTBreaker();

  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [confirm, setConfirm] = useState<ConfirmKind>(null);
  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  const toastSeq = useRef(0);

  function notify(text: string, tone: "success" | "error", key?: string) {
    toastSeq.current += 1;
    setToast({ id: toastSeq.current, text, tone, key });
  }

  const status = statusQuery.data;
  const orphans = orphansQuery.data?.items ?? [];

  const selectedList = useMemo(() => Array.from(selected), [selected]);

  function toggleSelected(uuid: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(uuid)) {
        next.delete(uuid);
      } else {
        next.add(uuid);
      }
      return next;
    });
  }

  async function handleProbe() {
    try {
      await probe.mutateAsync();
      notify(t("admin.dt.toast.health_checked"), "success", "health_checked");
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
    }
  }

  async function handleResetBreaker() {
    try {
      await resetBreaker.mutateAsync();
      notify(t("admin.dt.toast.breaker_reset"), "success", "breaker_reset");
      setConfirm(null);
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
      setConfirm(null);
    }
  }

  // Reset is only meaningful when the breaker is OPEN or HALF_OPEN. Disabling
  // the button on CLOSED matches the backend's 409 contract — the operator
  // sees the affordance fade out instead of clicking through to a
  // dt_breaker_already_closed toast.
  const breakerResetEnabled =
    status?.state === "open" || status?.state === "half_open";

  async function handleCleanup(scope: "selected" | "all") {
    try {
      await cleanup.mutateAsync(
        scope === "selected" ? { dt_project_uuids: selectedList } : undefined,
      );
      notify(
        t("admin.dt.toast.cleanup_enqueued"),
        "success",
        "cleanup_enqueued",
      );
      setSelected(new Set());
      setConfirm(null);
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
    }
  }

  return (
    <div className="flex h-full flex-col" data-testid="admin-dt-page">
      <header className="border-b bg-card px-6 py-4">
        <h1 className="text-lg font-semibold tracking-tight">
          {t("admin.dt.title")}
        </h1>
        <p className="text-sm text-muted-foreground">{t("admin.dt.subtitle")}</p>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
        {/* Status card */}
        <section
          className="rounded-md border bg-card p-4"
          data-testid="admin-dt-status-card"
        >
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">
              {t("admin.dt.status.card_title")}
            </h2>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => statusQuery.refetch()}
                data-testid="admin-dt-refresh"
                disabled={statusQuery.isFetching}
              >
                <RefreshCw
                  className={cn(
                    "h-4 w-4",
                    statusQuery.isFetching && "animate-spin",
                  )}
                  aria-hidden
                />
                {t("admin.dt.actions.refresh")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setConfirm("reset_breaker")}
                disabled={!breakerResetEnabled || resetBreaker.isPending}
                data-testid="admin-dt-reset-breaker"
                title={
                  breakerResetEnabled
                    ? undefined
                    : t("admin.dt.breaker.reset.disabled_hint")
                }
              >
                {resetBreaker.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <RotateCcw className="h-4 w-4" aria-hidden />
                )}
                {t("admin.dt.breaker.reset.label")}
              </Button>
              <Button
                size="sm"
                onClick={handleProbe}
                disabled={probe.isPending}
                data-testid="admin-dt-force-probe"
              >
                {probe.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : null}
                {t("admin.dt.actions.force_health_check")}
              </Button>
            </div>
          </div>

          {confirm === "reset_breaker" ? (
            <div
              className="mb-3 flex flex-col gap-2 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-900"
              data-testid="admin-dt-reset-confirm-strip"
            >
              <p className="font-semibold">
                {t("admin.dt.breaker.reset.confirm_title")}
              </p>
              <p>{t("admin.dt.breaker.reset.confirm_body")}</p>
              <div className="flex justify-end gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setConfirm(null)}
                  data-testid="admin-dt-reset-confirm-cancel"
                >
                  {t("admin.actions.cancel")}
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={handleResetBreaker}
                  disabled={resetBreaker.isPending}
                  data-testid="admin-dt-reset-confirm-ok"
                >
                  {resetBreaker.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : null}
                  {t("admin.actions.confirm")}
                </Button>
              </div>
            </div>
          ) : null}

          {statusQuery.isLoading ? (
            <div className="space-y-2" data-testid="admin-dt-status-loading">
              <Skeleton className="h-6 w-1/3" />
              <Skeleton className="h-6 w-1/2" />
              <Skeleton className="h-6 w-1/4" />
            </div>
          ) : statusQuery.isError ? (
            <Alert variant="destructive" data-testid="admin-dt-status-error">
              <AlertDescription>{t("admin.errors.unknown")}</AlertDescription>
            </Alert>
          ) : status ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="flex flex-col gap-2">
                <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
                  {t("admin.dt.status.state_label")}
                </span>
                <BreakerBadge state={status.state} />
              </div>
              <MetaRow
                label={t("admin.dt.status.fail_count_label")}
                value={String(status.fail_count)}
                testId="admin-dt-fail-count"
              />
              <MetaRow
                label={t("admin.dt.status.version_label")}
                value={status.version ?? t("admin.dt.status.no_version")}
                testId="admin-dt-version"
              />
              <MetaRow
                label={t("admin.dt.status.last_check_at_label")}
                value={formatRelativeToNow(
                  status.last_check_at,
                  i18n.resolvedLanguage,
                )}
                testId="admin-dt-last-check"
              />
              <MetaRow
                label={t("admin.dt.status.last_error_label")}
                value={status.last_error ?? t("admin.dt.status.no_error")}
                testId="admin-dt-last-error"
              />
              <MetaRow
                label={t("admin.dt.status.auto_restart_label")}
                value={
                  status.auto_restart_attempted
                    ? t("admin.dt.status.yes")
                    : t("admin.dt.status.no")
                }
                testId="admin-dt-auto-restart"
              />
            </div>
          ) : null}
        </section>

        {/* Orphans card */}
        <section
          className="rounded-md border bg-card p-4"
          data-testid="admin-dt-orphans-card"
        >
          <div className="mb-3 flex items-start justify-between">
            <div>
              <h2 className="text-sm font-semibold">
                {t("admin.dt.orphans.card_title")}
              </h2>
              <p className="text-xs text-muted-foreground">
                {t("admin.dt.orphans.card_subtitle")}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => orphansQuery.refetch()}
                disabled={orphansQuery.isFetching}
                data-testid="admin-dt-orphans-refresh"
              >
                <RefreshCw
                  className={cn(
                    "h-4 w-4",
                    orphansQuery.isFetching && "animate-spin",
                  )}
                  aria-hidden
                />
                {t("admin.dt.actions.refresh")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setConfirm("cleanup_selected")}
                disabled={selected.size === 0 || cleanup.isPending}
                data-testid="admin-dt-cleanup-selected"
              >
                <Trash2 className="h-4 w-4" aria-hidden />
                {t("admin.dt.actions.cleanup_selected")}
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => setConfirm("cleanup_all")}
                disabled={cleanup.isPending}
                data-testid="admin-dt-cleanup-all"
              >
                {t("admin.dt.actions.cleanup_all")}
              </Button>
            </div>
          </div>

          {confirm === "cleanup_selected" || confirm === "cleanup_all" ? (
            <div
              className="mb-3 flex flex-col gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
              data-testid="admin-dt-confirm-strip"
              data-kind={confirm}
            >
              <p>
                {t(
                  confirm === "cleanup_selected"
                    ? "admin.dt.confirm.cleanup"
                    : "admin.dt.confirm.cleanup_all",
                )}
              </p>
              <div className="flex justify-end gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setConfirm(null)}
                  data-testid="admin-dt-confirm-cancel"
                >
                  {t("admin.actions.cancel")}
                </Button>
                <Button
                  size="sm"
                  onClick={() =>
                    handleCleanup(
                      confirm === "cleanup_selected" ? "selected" : "all",
                    )
                  }
                  disabled={cleanup.isPending}
                  data-testid="admin-dt-confirm-ok"
                >
                  {cleanup.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : null}
                  {t("admin.actions.confirm")}
                </Button>
              </div>
            </div>
          ) : null}

          {orphansQuery.isError ? (
            <Alert variant="destructive" data-testid="admin-dt-orphans-error">
              <AlertDescription>
                {t(adminErrorMessageKey(orphansQuery.error))}
              </AlertDescription>
            </Alert>
          ) : (
            <table
              className="w-full text-sm"
              data-testid="admin-dt-orphans-table"
              aria-busy={orphansQuery.isLoading}
            >
              <thead className="bg-muted/30">
                <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="w-10 px-3 py-2">
                    <span className="sr-only">
                      {t("admin.dt.orphans.select_label")}
                    </span>
                  </th>
                  <th className="px-3 py-2">
                    {t("admin.dt.orphans.column.uuid")}
                  </th>
                  <th className="px-3 py-2">
                    {t("admin.dt.orphans.column.name")}
                  </th>
                  <th className="px-3 py-2">
                    {t("admin.dt.orphans.column.version")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {orphansQuery.isLoading
                  ? Array.from({ length: 4 }).map((_, i) => (
                      <tr key={`skeleton-${i}`} className="border-b">
                        <td className="px-3 py-2" colSpan={4}>
                          <Skeleton className="h-5 w-full" />
                        </td>
                      </tr>
                    ))
                  : orphans.map((row) => (
                      <tr
                        key={row.dt_project_uuid}
                        data-testid="admin-dt-orphan-row"
                        data-uuid={row.dt_project_uuid}
                        data-selected={selected.has(row.dt_project_uuid)}
                        className="border-b transition-colors hover:bg-accent/40"
                        style={{ height: "var(--table-row)" }}
                      >
                        <td className="px-3">
                          <input
                            type="checkbox"
                            data-testid="admin-dt-orphan-checkbox"
                            data-uuid={row.dt_project_uuid}
                            checked={selected.has(row.dt_project_uuid)}
                            onChange={() =>
                              toggleSelected(row.dt_project_uuid)
                            }
                            aria-label={t("admin.dt.orphans.select_label")}
                          />
                        </td>
                        <td className="px-3 font-mono text-xs">
                          {row.dt_project_uuid}
                        </td>
                        <td className="px-3">
                          {row.dt_project_name ?? "—"}
                        </td>
                        <td className="px-3 text-xs text-muted-foreground">
                          {row.dt_project_version ?? "—"}
                        </td>
                      </tr>
                    ))}
                {!orphansQuery.isLoading && orphans.length === 0 ? (
                  <tr>
                    <td
                      colSpan={4}
                      className="px-3 py-8 text-center text-sm text-muted-foreground"
                      data-testid="admin-dt-orphans-empty"
                    >
                      {t("admin.dt.orphans.empty")}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          )}
        </section>
      </div>

      <AdminToast message={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
