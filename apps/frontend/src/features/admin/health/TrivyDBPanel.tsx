/**
 * TrivyDBPanel — admin/health Trivy DB status panel (W6-#43e).
 *
 * Renders a Card with a 4-KPI grid (Last update, Vulnerabilities tracked,
 * DB version, Next refresh in {X}), a freshness badge, and a metadata
 * footer (cache directory + DB repository). On the "not yet downloaded"
 * case (`last_update === null` or `freshness === "unknown"`), the panel
 * renders the W11-G `EmptyState` primitive instead of the KPI grid so the
 * operator sees an explicit "waiting for next worker boot" affordance.
 *
 * Visual contract:
 *   - Card: shadow-sm + bg-card + rounded-lg + border (W11 tokens applied
 *     automatically via the existing primitive set).
 *   - Heading: text-sm semibold (matches the admin/health card heading).
 *   - Freshness badge: emerald / amber / red mirroring the existing
 *     statusVisuals() pattern in AdminHealthPage so operators have one
 *     mental model for both surfaces.
 *   - KPI grid: 2 columns on mobile, 4 columns on lg+. Compact (no padding
 *     blowouts) so the panel slots cleanly above the probe grid.
 *   - Metadata footer: muted text-xs mono so cache_dir / repository read
 *     as operator config, not narrative copy.
 *
 * Accessibility:
 *   - `role="status"` on the panel root via the EmptyState (when empty)
 *     and a vanilla section otherwise — the badge already pairs an icon
 *     with the i18n text so colour is not the only cue.
 *   - Absolute timestamp surfaced via the badge `title` attribute so
 *     screen-reader users still get the precise ISO string.
 */
import {
  AlertCircle,
  CheckCircle2,
  Database,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type TrivyDbFreshness,
  type TrivyDbStatus,
} from "@/features/admin/health/api/adminTrivyHealthApi";
import { useAdminTrivyHealth } from "@/features/admin/health/api/useAdminTrivyHealth";
import { adminErrorMessageKey } from "@/features/admin/lib/adminErrorMessage";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";
import { formatBytes } from "@/lib/zipFolder";

const DASH = "—";

function freshnessVisuals(freshness: TrivyDbFreshness): {
  icon: typeof CheckCircle2;
  badge: string;
} {
  if (freshness === "fresh") {
    return {
      icon: CheckCircle2,
      badge: "border-emerald-300 bg-emerald-50 text-emerald-700",
    };
  }
  if (freshness === "stale") {
    return {
      icon: ShieldAlert,
      badge: "border-amber-300 bg-amber-50 text-amber-800",
    };
  }
  // very_stale — unknown never reaches here because the empty state owns it.
  return {
    icon: AlertCircle,
    badge: "border-red-300 bg-red-50 text-red-700",
  };
}

function formatVulnCount(count: number | null, locale?: string): string {
  if (count == null) return DASH;
  // Locale-aware thousands separator. `Intl.NumberFormat` is widely supported
  // and avoids us hard-coding a comma for KO users (whose locale also uses
  // commas, but the choice is the platform's, not ours).
  return new Intl.NumberFormat(locale).format(count);
}

/**
 * Format `next_refresh_at` as a relative duration the FE can drop into the
 * "Next refresh in {{value}}" i18n key. Returns the dash for null.
 */
function formatNextRefresh(value: string | null, locale?: string): string {
  if (!value) return DASH;
  return formatRelativeToNow(value, locale);
}

interface KpiTileProps {
  label: string;
  value: string;
  tooltip?: string;
  testId: string;
}

function KpiTile({ label, value, tooltip, testId }: KpiTileProps) {
  return (
    <div className="rounded-md border bg-background p-3" data-testid={testId}>
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

interface TrivyDBPanelProps {
  /** Optional override for unit tests so the relative-time output is deterministic. */
  now?: number;
}

export function TrivyDBPanel({ now }: TrivyDBPanelProps = {}) {
  const { t, i18n } = useTranslation("admin");
  const query = useAdminTrivyHealth();
  const locale = i18n.resolvedLanguage;

  const renderHeading = () => (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <Database className="h-4 w-4 text-muted-foreground" aria-hidden />
        {t("admin.trivy_db.heading")}
      </h2>
      {query.isFetching ? (
        <RefreshCw
          className="h-3 w-3 animate-spin text-muted-foreground"
          aria-hidden
          data-testid="admin-trivy-db-fetching"
        />
      ) : null}
    </div>
  );

  if (query.isLoading) {
    return (
      <section
        className="rounded-lg border bg-card p-4 shadow-sm"
        data-testid="admin-trivy-db-panel"
        aria-busy
      >
        {renderHeading()}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={`trivy-skeleton-${i}`}
              className="rounded-md border bg-background p-3"
              data-testid="admin-trivy-db-skeleton"
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
        data-testid="admin-trivy-db-panel"
      >
        {renderHeading()}
        <Alert variant="destructive" data-testid="admin-trivy-db-error">
          <AlertDescription>
            {t(adminErrorMessageKey(query.error))}
          </AlertDescription>
        </Alert>
      </section>
    );
  }

  const data: TrivyDbStatus | undefined = query.data;
  if (!data) {
    return null;
  }

  // Empty state: the worker has not downloaded the DB yet. The backend
  // surfaces this as ``last_update === null`` *or* ``freshness === "unknown"``
  // (the service degrades gracefully if the cache directory is unreadable
  // and still returns ``unknown`` instead of a 500). We render the W11-G
  // EmptyState so the operator sees the explicit "Trivy DB not yet
  // downloaded" affordance — same visual language as Projects/Scans zero
  // states.
  const isEmpty = data.last_update === null || data.freshness === "unknown";

  if (isEmpty) {
    return (
      <section
        className="rounded-lg border bg-card p-4 shadow-sm"
        data-testid="admin-trivy-db-panel"
        data-status="empty"
      >
        {renderHeading()}
        <EmptyState
          icon={<Database aria-hidden />}
          title={t("admin.trivy_db.empty.title")}
          description={t("admin.trivy_db.empty.description")}
          data-testid="admin-trivy-db-empty"
        />
        <div
          className="mt-2 flex flex-col gap-1 border-t pt-3 font-mono text-xs text-muted-foreground"
          data-testid="admin-trivy-db-footer"
        >
          <p>
            <span className="font-sans font-medium">
              {t("admin.trivy_db.footer.cache_dir")}:
            </span>{" "}
            {data.cache_dir}
          </p>
          <p>
            <span className="font-sans font-medium">
              {t("admin.trivy_db.footer.repository")}:
            </span>{" "}
            {data.repository}
          </p>
        </div>
      </section>
    );
  }

  // Happy path — render the freshness badge + KPI grid + metadata footer.
  const visuals = freshnessVisuals(data.freshness);
  const Icon = visuals.icon;
  const lastUpdateAbs = data.last_update ?? "";
  const nextRefreshValue = formatNextRefresh(data.next_refresh_at, locale);

  return (
    <section
      className="rounded-lg border bg-card p-4 shadow-sm"
      data-testid="admin-trivy-db-panel"
      data-status={data.freshness}
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Database className="h-4 w-4 text-muted-foreground" aria-hidden />
          {t("admin.trivy_db.heading")}
        </h2>
        <Badge
          variant="outline"
          className={cn("gap-1 text-xs", visuals.badge)}
          data-testid="admin-trivy-db-freshness-badge"
          data-freshness={data.freshness}
        >
          <Icon className="h-3 w-3" aria-hidden />
          {t(`admin.trivy_db.freshness.${data.freshness}`)}
        </Badge>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiTile
          label={t("admin.trivy_db.kpi.last_update")}
          value={
            data.last_update
              ? formatRelativeToNow(data.last_update, locale, now)
              : DASH
          }
          tooltip={lastUpdateAbs}
          testId="admin-trivy-db-kpi-last-update"
        />
        <KpiTile
          label={t("admin.trivy_db.kpi.vuln_count")}
          value={formatVulnCount(data.vuln_count, locale)}
          testId="admin-trivy-db-kpi-vuln-count"
        />
        <KpiTile
          label={t("admin.trivy_db.kpi.db_version")}
          value={
            data.db_size_bytes != null
              ? `${data.db_version ?? DASH} (${formatBytes(data.db_size_bytes)})`
              : (data.db_version ?? DASH)
          }
          testId="admin-trivy-db-kpi-db-version"
        />
        <KpiTile
          label={t("admin.trivy_db.kpi.next_refresh", {
            value: nextRefreshValue,
          })}
          value={
            data.next_refresh_at
              ? formatRelativeToNow(data.next_refresh_at, locale, now)
              : DASH
          }
          tooltip={data.next_refresh_at ?? undefined}
          testId="admin-trivy-db-kpi-next-refresh"
        />
      </div>

      <div
        className="mt-3 flex flex-col gap-1 border-t pt-3 font-mono text-xs text-muted-foreground"
        data-testid="admin-trivy-db-footer"
      >
        <p>
          <span className="font-sans font-medium">
            {t("admin.trivy_db.footer.cache_dir")}:
          </span>{" "}
          {data.cache_dir}
        </p>
        <p>
          <span className="font-sans font-medium">
            {t("admin.trivy_db.footer.repository")}:
          </span>{" "}
          {data.repository}
        </p>
      </div>
    </section>
  );
}
