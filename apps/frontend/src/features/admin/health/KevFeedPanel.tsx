/**
 * KevFeedPanel — admin/health CISA KEV feed status panel (Phase C / C2).
 *
 * Structural mirror of {@link TrivyDBPanel}: a Card with a status badge, a
 * 4-KPI grid (Last sync / KEV-flagged vulnerabilities / Recently listed·
 * delisted / Next sync) and a metadata footer (feed host). Panel-level
 * status precedence:
 *
 *   1. `enabled === false`      → muted "Disabled" badge. The sync beat is
 *      switched off; KPI grid still renders whatever the last run left
 *      behind (or the EmptyState when it never ran).
 *   2. never ran (all-null row) → EmptyState primitive, same convention as
 *      the Trivy "not yet downloaded" branch. No result badge — there is no
 *      result to describe yet.
 *   3. `last_result === "skipped"` → amber badge + the raw `skipped_reason`
 *      line so the operator sees *why* (e.g. feed not modified).
 *   4. `last_result === "synced"`  → emerald badge.
 *
 * Visual contract matches TrivyDBPanel (shadow-sm card, text-sm semibold
 * heading, 2→4 column KPI grid, mono muted footer) so admin/health reads as
 * one surface. Badges pair an icon + i18n text — colour is never the only
 * signal (CLAUDE.md design system).
 *
 * e2e anchors: root `data-testid="kev-feed-panel"` + `data-status`; each KPI
 * tile carries `data-testid` and a raw `data-value` so Playwright can assert
 * against wire values instead of locale-formatted text.
 */
import {
  AlertCircle,
  CheckCircle2,
  CircleOff,
  RefreshCw,
  ShieldAlert,
  Siren,
} from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { type KevFeedStatus } from "@/features/admin/health/api/adminKevHealthApi";
import { useAdminKevHealth } from "@/features/admin/health/api/useAdminKevHealth";
import { adminErrorMessageKey } from "@/features/admin/lib/adminErrorMessage";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

const DASH = "—";

type PanelStatus = "disabled" | "skipped" | "synced";

function statusVisuals(status: PanelStatus): {
  icon: typeof CheckCircle2;
  badge: string;
} {
  if (status === "disabled") {
    // Muted — mirrors the Badge `muted` variant palette (BUG-001-safe slate).
    return {
      icon: CircleOff,
      badge: "border-border bg-muted text-slate-600",
    };
  }
  if (status === "skipped") {
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

function formatCount(count: number | null, locale?: string): string {
  if (count == null) return DASH;
  return new Intl.NumberFormat(locale).format(count);
}

/** "+{listed} / −{delisted}" for the delta KPI; dash when the run never wrote deltas. */
function formatListedDelisted(
  listed: number | null,
  delisted: number | null,
  locale?: string,
): string {
  if (listed == null && delisted == null) return DASH;
  return `+${formatCount(listed ?? 0, locale)} / −${formatCount(delisted ?? 0, locale)}`;
}

interface KpiTileProps {
  label: string;
  value: string;
  tooltip?: string;
  testId: string;
  /** Raw wire value for e2e assertions (`data-value`). */
  dataValue?: string | number | null;
}

function KpiTile({ label, value, tooltip, testId, dataValue }: KpiTileProps) {
  return (
    <div
      className="rounded-md border bg-background p-3"
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

interface KevFeedPanelProps {
  /** Optional override for unit tests so the relative-time output is deterministic. */
  now?: number;
}

export function KevFeedPanel({ now }: KevFeedPanelProps = {}) {
  const { t, i18n } = useTranslation("admin");
  const query = useAdminKevHealth();
  const locale = i18n.resolvedLanguage;

  const renderHeading = (badge?: ReactNode) => (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <Siren className="h-4 w-4 text-muted-foreground" aria-hidden />
        {t("admin.kev_feed.heading")}
      </h2>
      <div className="flex items-center gap-2">
        {query.isFetching ? (
          <RefreshCw
            className="h-3 w-3 animate-spin text-muted-foreground"
            aria-hidden
            data-testid="kev-feed-fetching"
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
        data-testid="kev-feed-panel"
        aria-busy
      >
        {renderHeading()}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={`kev-skeleton-${i}`}
              className="rounded-md border bg-background p-3"
              data-testid="kev-feed-skeleton"
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
        data-testid="kev-feed-panel"
      >
        {renderHeading()}
        <Alert variant="destructive" data-testid="kev-feed-error">
          <AlertDescription>
            {t(adminErrorMessageKey(query.error))}
          </AlertDescription>
        </Alert>
      </section>
    );
  }

  const data: KevFeedStatus | undefined = query.data;
  if (!data) {
    return null;
  }

  // Never-ran contract: no state row on the backend ⇒ every run field is
  // null. `last_attempt_at` is the canonical sentinel (a skipped run still
  // stamps it); we also require `last_result === null` so a degenerate
  // half-written row falls through to the KPI grid instead of hiding data.
  const neverRan = data.last_attempt_at === null && data.last_result === null;

  // Badge precedence: disabled > skipped > synced. The never-ran branch
  // shows a badge only when disabled (that config signal outranks "no runs
  // yet"); otherwise the EmptyState carries the message alone, mirroring
  // the TrivyDBPanel empty branch.
  const status: PanelStatus | null = !data.enabled
    ? "disabled"
    : data.last_result === "skipped"
      ? "skipped"
      : data.last_result === "synced"
        ? "synced"
        : null;

  const badge = status ? (
    (() => {
      const visuals = statusVisuals(status);
      const Icon = visuals.icon;
      return (
        <Badge
          variant="outline"
          className={cn("gap-1 text-xs", visuals.badge)}
          data-testid="kev-feed-status-badge"
          data-status={status}
        >
          <Icon className="h-3 w-3" aria-hidden />
          {t(`admin.kev_feed.status.${status}`)}
        </Badge>
      );
    })()
  ) : null;

  const footer = (
    <div
      className="mt-3 flex flex-col gap-1 border-t pt-3 font-mono text-xs text-muted-foreground"
      data-testid="kev-feed-footer"
    >
      <p>
        <span className="font-sans font-medium">
          {t("admin.kev_feed.footer.feed_host")}:
        </span>{" "}
        {data.feed_host}
      </p>
    </div>
  );

  if (neverRan) {
    return (
      <section
        className="rounded-lg border bg-card p-4 shadow-sm"
        data-testid="kev-feed-panel"
        data-status={status ?? "empty"}
      >
        {renderHeading(badge)}
        <EmptyState
          icon={<Siren aria-hidden />}
          title={t("admin.kev_feed.empty.title")}
          description={t("admin.kev_feed.empty.description")}
          data-testid="kev-feed-empty"
        />
        {footer}
      </section>
    );
  }

  return (
    <section
      className="rounded-lg border bg-card p-4 shadow-sm"
      data-testid="kev-feed-panel"
      data-status={status ?? "empty"}
    >
      {renderHeading(badge)}

      {status === "skipped" && data.skipped_reason ? (
        <p
          className="mb-3 flex items-center gap-1.5 text-xs text-amber-800"
          data-testid="kev-feed-skipped-reason"
        >
          <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("admin.kev_feed.skipped_reason", { reason: data.skipped_reason })}
        </p>
      ) : null}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiTile
          label={t("admin.kev_feed.kpi.last_synced")}
          value={
            data.last_synced_at
              ? formatRelativeToNow(data.last_synced_at, locale, now)
              : DASH
          }
          tooltip={data.last_synced_at ?? undefined}
          testId="kev-feed-kpi-last-synced"
          dataValue={data.last_synced_at}
        />
        <KpiTile
          label={t("admin.kev_feed.kpi.kev_flagged_total")}
          value={formatCount(data.kev_flagged_total, locale)}
          testId="kev-feed-kpi-flagged-total"
          dataValue={data.kev_flagged_total}
        />
        <KpiTile
          label={t("admin.kev_feed.kpi.listed_delisted")}
          value={formatListedDelisted(data.listed, data.delisted, locale)}
          testId="kev-feed-kpi-listed-delisted"
          dataValue={
            data.listed == null && data.delisted == null
              ? null
              : `${data.listed ?? 0}/${data.delisted ?? 0}`
          }
        />
        <KpiTile
          label={t("admin.kev_feed.kpi.next_refresh")}
          value={
            data.next_refresh_at
              ? formatRelativeToNow(data.next_refresh_at, locale, now)
              : DASH
          }
          tooltip={data.next_refresh_at ?? undefined}
          testId="kev-feed-kpi-next-refresh"
          dataValue={data.next_refresh_at}
        />
      </div>

      {footer}
    </section>
  );
}
