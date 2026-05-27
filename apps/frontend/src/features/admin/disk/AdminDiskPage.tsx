/**
 * AdminDiskPage — Phase 4 PR #14 §4.6.
 *
 * Three cards (workspace / postgres / redis). Each card shows:
 *   - Used / total / free in human-readable bytes.
 *   - A horizontal progress bar coloured against the threshold:
 *       used_pct ≥ threshold_critical → red    (down)
 *       used_pct ≥ threshold_warning  → orange (degraded)
 *       otherwise                     → green  (ok)
 *   - A status badge that pairs the colour with an i18n label so the
 *     signal is not colour-only (CLAUDE.md "Accessibility").
 *
 * When the backend reports a per-item `error` (path missing / permission
 * denied) the card surfaces an inline `Alert` instead of the gauge.
 */
import { AlertCircle, CheckCircle2, RefreshCw, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type AdminDiskItem,
  type DiskHealthStatus,
} from "@/features/admin/disk/api/adminDiskApi";
import { useAdminDisk } from "@/features/admin/disk/api/useAdminDisk";
import {
  adminErrorMessageKey,
} from "@/features/admin/lib/adminErrorMessage";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

function formatBytes(bytes: number | null): string {
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

function statusColors(status: DiskHealthStatus): {
  badge: string;
  bar: string;
  icon: typeof CheckCircle2;
} {
  if (status === "ok") {
    return {
      badge: "border-emerald-300 bg-emerald-50 text-emerald-700",
      bar: "bg-emerald-500",
      icon: CheckCircle2,
    };
  }
  if (status === "degraded") {
    return {
      badge: "border-amber-300 bg-amber-50 text-amber-800",
      bar: "bg-amber-500",
      icon: ShieldAlert,
    };
  }
  return {
    badge: "border-red-300 bg-red-50 text-red-700",
    bar: "bg-red-500",
    icon: AlertCircle,
  };
}

function DiskCard({ item }: { item: AdminDiskItem }) {
  const { t } = useTranslation("admin");
  const { badge, bar, icon: Icon } = statusColors(item.status);

  const usedPct = item.used_pct == null ? null : Math.min(100, item.used_pct);

  return (
    <article
      className="rounded-md border bg-card p-4"
      data-testid="admin-disk-card"
      data-card-name={item.name}
      data-status={item.status}
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">
            {t(`admin.disk.card.${item.name}`)}
          </h2>
          {item.path ? (
            <p
              className="font-mono text-[11px] text-muted-foreground"
              data-testid="admin-disk-path"
            >
              {item.path}
            </p>
          ) : null}
        </div>
        <Badge
          variant="outline"
          className={cn("gap-1 text-xs", badge)}
          data-testid="admin-disk-status-badge"
          data-status={item.status}
        >
          <Icon className="h-3 w-3" aria-hidden />
          {item.error
            ? t("admin.disk.status.unavailable")
            : t(`admin.disk.status.${item.status}`)}
        </Badge>
      </div>

      {item.error ? (
        <Alert
          variant="destructive"
          data-testid="admin-disk-error"
          className="mt-2"
        >
          <AlertDescription>
            {t("admin.disk.errors.unavailable")}
          </AlertDescription>
        </Alert>
      ) : (
        <>
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-muted"
            data-testid="admin-disk-bar-track"
          >
            <div
              className={cn("h-full transition-all", bar)}
              style={{ width: `${usedPct ?? 0}%` }}
              data-testid="admin-disk-bar-fill"
              data-used-pct={usedPct ?? 0}
              role="progressbar"
              aria-valuenow={usedPct ?? 0}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={t(`admin.disk.card.${item.name}`)}
            />
          </div>
          <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
            <Meta
              label={t("admin.disk.label.used")}
              value={`${formatBytes(item.used_bytes)}${
                usedPct != null ? ` (${usedPct.toFixed(1)}%)` : ""
              }`}
            />
            <Meta
              label={t("admin.disk.label.free")}
              value={formatBytes(item.free_bytes)}
            />
            <Meta
              label={t("admin.disk.label.total")}
              value={formatBytes(item.total_bytes)}
            />
          </div>
          <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
            <span>
              {t("admin.disk.label.warning_threshold")}{": "}
              {item.threshold_warning.toFixed(0)}%
            </span>
            <span>
              {t("admin.disk.label.critical_threshold")}{": "}
              {item.threshold_critical.toFixed(0)}%
            </span>
          </div>
        </>
      )}
    </article>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="font-mono text-xs">{value}</div>
    </div>
  );
}

export function AdminDiskPage() {
  const { t, i18n } = useTranslation("admin");
  const diskQuery = useAdminDisk();

  const items = diskQuery.data?.items ?? [];
  const collectedAt = diskQuery.data?.collected_at;

  return (
    <div className="flex h-full flex-col" data-testid="admin-disk-page">
      <header className="flex items-start justify-between border-b bg-card px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">
            {t("admin.disk.title")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("admin.disk.subtitle")}
          </p>
          {collectedAt ? (
            <p
              className="mt-1 text-xs text-muted-foreground"
              data-testid="admin-disk-collected-at"
            >
              {t("admin.disk.collected_at", {
                when: formatRelativeToNow(collectedAt, i18n.resolvedLanguage),
              })}
            </p>
          ) : null}
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => diskQuery.refetch()}
          disabled={diskQuery.isFetching}
          data-testid="admin-disk-refresh"
        >
          <RefreshCw
            className={cn(
              "h-4 w-4",
              diskQuery.isFetching && "animate-spin",
            )}
            aria-hidden
          />
          {t("admin.disk.actions.refresh")}
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {diskQuery.isError ? (
          <Alert variant="destructive" data-testid="admin-disk-page-error">
            <AlertDescription>
              {t(adminErrorMessageKey(diskQuery.error))}
            </AlertDescription>
          </Alert>
        ) : null}

        <div
          className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-2"
          data-testid="admin-disk-grid"
          aria-busy={diskQuery.isLoading}
        >
          {diskQuery.isLoading
            ? Array.from({ length: 3 }).map((_, i) => (
                <div
                  key={`skeleton-${i}`}
                  className="rounded-md border bg-card p-4"
                  data-testid="admin-disk-card-skeleton"
                >
                  <Skeleton className="mb-2 h-5 w-1/3" />
                  <Skeleton className="mb-3 h-2 w-full" />
                  <Skeleton className="h-4 w-2/3" />
                </div>
              ))
            : items.map((item) => <DiskCard key={item.name} item={item} />)}
        </div>
      </div>
    </div>
  );
}
