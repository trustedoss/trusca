// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * MaliciousPanel — admin/health malicious-snapshot status panel (#26).
 *
 * Sibling of {@link EolPanel}, with the same status precedence (disabled →
 * stale → skipped → ok) and two differences that matter.
 *
 * The staleness window is 60 days rather than 180. End-of-life dates move on
 * a product's release calendar; malicious advisories are published daily, so
 * a two-month-old snapshot has stopped answering the question it is asked.
 * The backend owns the threshold and sends `snapshot_stale`, so the panel
 * does not recompute it — the number can be tuned per deployment.
 *
 * The KPI grid leads with `newly_flagged` rather than the running total. That
 * is the count this beat exists to produce: packages already in stock that an
 * advisory caught up with, which no scan would have surfaced because nobody
 * re-scans an old release.
 *
 * e2e anchors: root `data-testid="malicious-panel"` + `data-status`; each KPI
 * tile carries `data-testid` + raw `data-value`.
 */
import {
  AlertCircle,
  CheckCircle2,
  CircleOff,
  RefreshCw,
  ShieldAlert,
  ShieldX,
} from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { type MaliciousStatus } from "@/features/admin/health/api/adminMaliciousHealthApi";
import { useAdminMaliciousHealth } from "@/features/admin/health/api/useAdminMaliciousHealth";
import { adminErrorMessageKey } from "@/features/admin/lib/adminErrorMessage";
import { cn } from "@/lib/utils";

type PanelStatus = "ok" | "stale" | "skipped" | "disabled";

function statusVisuals(status: PanelStatus) {
  switch (status) {
    case "disabled":
      return { icon: CircleOff, badge: "text-muted-foreground" };
    case "stale":
    case "skipped":
      return {
        icon: AlertCircle,
        badge: "border-status-warning text-status-warning-foreground",
      };
    default:
      return {
        icon: CheckCircle2,
        badge: "border-status-success text-status-success-foreground",
      };
  }
}

function formatDateTime(value: string | null, locale: string | undefined) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

interface KpiProps {
  label: string;
  value: string;
  rawValue: string | number | null;
  testid: string;
  emphasize?: boolean;
  hint?: string;
}

function Kpi({ label, value, rawValue, testid, emphasize, hint }: KpiProps) {
  return (
    <div
      className="rounded-md border bg-background p-3"
      data-testid={testid}
      data-value={rawValue ?? ""}
    >
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={cn(
          "mt-1 text-sm font-semibold tabular-nums",
          emphasize && "text-risk-critical-foreground",
        )}
      >
        {value}
      </p>
      {hint ? <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export function MaliciousPanel() {
  const { t, i18n } = useTranslation("admin");
  const query = useAdminMaliciousHealth();
  const locale = i18n.resolvedLanguage;

  const renderHeading = (badge?: ReactNode) => (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <ShieldX className="h-4 w-4 text-muted-foreground" aria-hidden />
        {t("admin.malicious.heading")}
      </h2>
      <div className="flex items-center gap-2">
        {query.isFetching ? (
          <RefreshCw
            className="h-3 w-3 animate-spin text-muted-foreground"
            aria-hidden
            data-testid="malicious-fetching"
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
        data-testid="malicious-panel"
        aria-busy
      >
        {renderHeading()}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={`malicious-skeleton-${i}`}
              className="rounded-md border bg-background p-3"
              data-testid="malicious-skeleton"
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
        data-testid="malicious-panel"
      >
        {renderHeading()}
        <Alert variant="destructive" data-testid="malicious-error">
          <AlertDescription>
            {t(adminErrorMessageKey(query.error))}
          </AlertDescription>
        </Alert>
      </section>
    );
  }

  const data: MaliciousStatus | undefined = query.data;
  if (!data) return null;

  const status: PanelStatus = !data.enabled
    ? "disabled"
    : data.snapshot_stale
      ? "stale"
      : data.last_result === "skipped" &&
          data.skipped_reason !== "refresh_disabled"
        ? "skipped"
        : "ok";

  const visuals = statusVisuals(status);
  const StatusIcon = visuals.icon;

  return (
    <section
      className="rounded-lg border bg-card p-4 shadow-sm"
      data-testid="malicious-panel"
      data-status={status}
    >
      {renderHeading(
        <Badge
          variant="outline"
          className={cn("gap-1 text-xs", visuals.badge)}
          data-testid="malicious-status-badge"
          data-status={status}
        >
          <StatusIcon className="h-3 w-3" aria-hidden />
          {t(`admin.malicious.status.${status}`)}
        </Badge>,
      )}

      {status === "stale" ? (
        <p
          className="mb-3 flex items-center gap-1.5 text-xs text-status-warning-foreground"
          data-testid="malicious-stale-note"
        >
          <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("admin.malicious.stale_note")}
        </p>
      ) : null}

      {data.newly_flagged ? (
        <p
          className="mb-3 flex items-center gap-1.5 text-xs text-risk-critical-foreground"
          data-testid="malicious-newly-flagged-note"
        >
          <ShieldAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("admin.malicious.newly_flagged_note", {
            count: data.newly_flagged,
          })}
        </p>
      ) : null}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi
          label={t("admin.malicious.kpi.newly_flagged")}
          value={data.newly_flagged?.toLocaleString(locale) ?? "—"}
          rawValue={data.newly_flagged}
          testid="malicious-kpi-newly-flagged"
          emphasize={Boolean(data.newly_flagged)}
        />
        <Kpi
          label={t("admin.malicious.kpi.flagged_total")}
          value={data.flagged_total?.toLocaleString(locale) ?? "—"}
          rawValue={data.flagged_total}
          testid="malicious-kpi-flagged-total"
          emphasize={Boolean(data.flagged_total)}
        />
        <Kpi
          label={t("admin.malicious.kpi.snapshot")}
          value={data.snapshot_date ?? "—"}
          rawValue={data.snapshot_date}
          testid="malicious-kpi-snapshot"
          hint={t("admin.malicious.kpi.snapshot_hint", {
            count: data.purl_count,
          })}
        />
        <Kpi
          label={t("admin.malicious.kpi.next_tick")}
          value={formatDateTime(data.next_refresh_at, locale)}
          rawValue={data.next_refresh_at}
          testid="malicious-kpi-next-tick"
          hint={
            data.refresh_enabled
              ? t("admin.malicious.kpi.refresh_on")
              : t("admin.malicious.kpi.refresh_off")
          }
        />
      </div>

      <p
        className="mt-3 font-mono text-[11px] text-muted-foreground"
        data-testid="malicious-footer"
      >
        {t("admin.malicious.footer", {
          ecosystems: data.ecosystems.length,
          stamped: data.stamped ?? 0,
          last: formatDateTime(data.last_attempt_at, locale),
        })}
      </p>
    </section>
  );
}
