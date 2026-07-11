/**
 * EolPanel — admin/health endoflife.date snapshot status panel (Phase M).
 *
 * Structural mirror of {@link KevFeedPanel}: a Card with a status badge, a
 * 4-KPI grid (Snapshot date / EOL-flagged components / Stamped·cleared /
 * Next tick) and a mono footer (feed host + dataset origin). Panel-level
 * status precedence:
 *
 *   1. `enabled === false`         → muted "Disabled" badge (flagging off).
 *   2. snapshot older than 180 days → amber "Stale" badge — the staleness
 *      signal the plan calls for: on the default (no live refresh) posture
 *      the snapshot only moves with releases, so an operator sitting on a
 *      half-year-old dataset should see it at a glance.
 *   3. `last_result === "skipped"` → amber badge + raw skip reason.
 *   4. otherwise                   → emerald "OK" badge — unlike the KEV
 *      panel there is no never-ran EmptyState: the vendored snapshot ships
 *      with every release, so the dataset side always has content and only
 *      the beat KPIs show dashes before the first tick.
 *
 * e2e anchors: root `data-testid="eol-panel"` + `data-status`; each KPI tile
 * carries `data-testid` + raw `data-value`.
 */
import {
  AlertCircle,
  CalendarClock,
  CheckCircle2,
  CircleOff,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { type EolStatus } from "@/features/admin/health/api/adminEolHealthApi";
import { useAdminEolHealth } from "@/features/admin/health/api/useAdminEolHealth";
import { adminErrorMessageKey } from "@/features/admin/lib/adminErrorMessage";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

const DASH = "—";

/** Snapshot age past which the panel escalates to the amber Stale badge. */
const STALE_AFTER_DAYS = 180;

type PanelStatus = "disabled" | "stale" | "skipped" | "ok";

function statusVisuals(status: PanelStatus): {
  icon: typeof CheckCircle2;
  badge: string;
} {
  if (status === "disabled") {
    return {
      icon: CircleOff,
      badge: "border-border bg-muted text-slate-600",
    };
  }
  if (status === "stale" || status === "skipped") {
    return {
      icon: ShieldAlert,
      badge: "border-amber-300 bg-amber-50 text-amber-800",
    };
  }
  return {
    icon: CheckCircle2,
    badge: "border-emerald-300 bg-emerald-50 text-emerald-700",
  };
}

function snapshotAgeDays(snapshotDate: string | null, now: number): number | null {
  if (!snapshotDate) return null;
  const parsed = Date.parse(`${snapshotDate}T00:00:00Z`);
  if (Number.isNaN(parsed)) return null;
  return Math.floor((now - parsed) / (24 * 60 * 60 * 1000));
}

function formatCount(count: number | null, locale?: string): string {
  if (count == null) return DASH;
  return new Intl.NumberFormat(locale).format(count);
}

function formatStampedCleared(
  stamped: number | null,
  cleared: number | null,
  locale?: string,
): string {
  if (stamped == null && cleared == null) return DASH;
  return `+${formatCount(stamped ?? 0, locale)} / −${formatCount(cleared ?? 0, locale)}`;
}

interface KpiTileProps {
  label: string;
  value: string;
  tooltip?: string;
  testId: string;
  dataValue?: string | number | null;
  accent?: boolean;
}

function KpiTile({ label, value, tooltip, testId, dataValue, accent }: KpiTileProps) {
  return (
    <div
      className={cn(
        "rounded-md border bg-background p-3",
        accent && "border-amber-300",
      )}
      data-testid={testId}
      data-value={dataValue ?? undefined}
    >
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p
        className="mt-1 truncate text-sm font-semibold text-foreground"
        title={tooltip}
      >
        {value}
      </p>
    </div>
  );
}

interface EolPanelProps {
  /** Optional clock override for deterministic unit tests. */
  now?: number;
}

export function EolPanel({ now }: EolPanelProps = {}) {
  const { t, i18n } = useTranslation("admin");
  const query = useAdminEolHealth();
  const locale = i18n.resolvedLanguage;
  const clock = now ?? Date.now();

  const renderHeading = (badge?: ReactNode) => (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <CalendarClock className="h-4 w-4 text-muted-foreground" aria-hidden />
        {t("admin.eol.heading")}
      </h2>
      <div className="flex items-center gap-2">
        {query.isFetching ? (
          <RefreshCw
            className="h-3 w-3 animate-spin text-muted-foreground"
            aria-hidden
            data-testid="eol-fetching"
          />
        ) : null}
        {badge}
      </div>
    </div>
  );

  if (query.isLoading) {
    return (
      <section
        className="rounded-lg border bg-card p-4 shadow-sm"
        data-testid="eol-panel"
        aria-busy
      >
        {renderHeading()}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={`eol-skeleton-${i}`}
              className="rounded-md border bg-background p-3"
              data-testid="eol-skeleton"
            >
              <Skeleton className="mb-2 h-3 w-1/2" />
              <Skeleton className="h-4 w-3/4" />
            </div>
          ))}
        </div>
      </section>
    );
  }

  if (query.isError) {
    return (
      <section
        className="rounded-lg border bg-card p-4 shadow-sm"
        data-testid="eol-panel"
      >
        {renderHeading()}
        <Alert variant="destructive" data-testid="eol-error">
          <AlertDescription>
            {t(adminErrorMessageKey(query.error))}
          </AlertDescription>
        </Alert>
      </section>
    );
  }

  const data: EolStatus | undefined = query.data;
  if (!data) {
    return null;
  }

  const ageDays = snapshotAgeDays(data.snapshot_date, clock);
  const isStale = ageDays != null && ageDays > STALE_AFTER_DAYS;

  const status: PanelStatus = !data.enabled
    ? "disabled"
    : isStale
      ? "stale"
      : data.last_result === "skipped"
        ? "skipped"
        : "ok";

  const visuals = statusVisuals(status);
  const StatusIcon = visuals.icon;
  const badge = (
    <Badge
      variant="outline"
      className={cn("gap-1 text-xs", visuals.badge)}
      data-testid="eol-status-badge"
      data-status={status}
    >
      <StatusIcon className="h-3 w-3" aria-hidden />
      {t(`admin.eol.status.${status}`)}
    </Badge>
  );

  return (
    <section
      className="rounded-lg border bg-card p-4 shadow-sm"
      data-testid="eol-panel"
      data-status={status}
    >
      {renderHeading(badge)}

      {status === "stale" ? (
        <p
          className="mb-3 flex items-center gap-1.5 text-xs text-amber-800"
          data-testid="eol-stale-note"
        >
          <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("admin.eol.stale_note", { days: ageDays })}
        </p>
      ) : null}
      {status === "skipped" && data.skipped_reason ? (
        <p
          className="mb-3 flex items-center gap-1.5 text-xs text-amber-800"
          data-testid="eol-skipped-reason"
        >
          <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("admin.eol.skipped_reason", { reason: data.skipped_reason })}
        </p>
      ) : null}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiTile
          label={t("admin.eol.kpi.snapshot_date")}
          value={data.snapshot_date ?? DASH}
          tooltip={
            ageDays != null
              ? t("admin.eol.kpi.snapshot_age", { days: ageDays })
              : undefined
          }
          testId="eol-kpi-snapshot-date"
          dataValue={data.snapshot_date}
          accent={isStale}
        />
        <KpiTile
          label={t("admin.eol.kpi.flagged_total")}
          value={formatCount(data.eol_flagged_total, locale)}
          testId="eol-kpi-flagged-total"
          dataValue={data.eol_flagged_total}
        />
        <KpiTile
          label={t("admin.eol.kpi.stamped_cleared")}
          value={formatStampedCleared(data.stamped, data.cleared, locale)}
          testId="eol-kpi-stamped-cleared"
          dataValue={
            data.stamped == null && data.cleared == null
              ? null
              : `${data.stamped ?? 0}/${data.cleared ?? 0}`
          }
        />
        <KpiTile
          label={t("admin.eol.kpi.next_refresh")}
          value={
            data.next_refresh_at
              ? formatRelativeToNow(data.next_refresh_at, locale, now)
              : DASH
          }
          tooltip={data.next_refresh_at ?? undefined}
          testId="eol-kpi-next-refresh"
          dataValue={data.next_refresh_at}
        />
      </div>

      <div
        className="mt-3 flex flex-col gap-1 border-t pt-3 font-mono text-xs text-muted-foreground"
        data-testid="eol-footer"
      >
        <p>
          <span className="font-sans font-medium">
            {t("admin.eol.footer.origin")}:
          </span>{" "}
          {data.snapshot_origin
            ? t(`admin.eol.origin.${data.snapshot_origin}`)
            : DASH}
          {" · "}
          <span className="font-sans font-medium">
            {t("admin.eol.footer.products")}:
          </span>{" "}
          {formatCount(data.product_count, locale)}
        </p>
        <p>
          <span className="font-sans font-medium">
            {t("admin.eol.footer.refresh")}:
          </span>{" "}
          {data.refresh_enabled
            ? t("admin.eol.footer.refresh_on", { host: data.feed_host ?? DASH })
            : t("admin.eol.footer.refresh_off")}
        </p>
      </div>
    </section>
  );
}
