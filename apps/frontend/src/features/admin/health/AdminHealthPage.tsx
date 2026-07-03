/**
 * AdminHealthPage — Phase 4 PR #14 §4.8.
 *
 * Compact card grid (postgres / redis / celery / dt / disk / active_scans /
 * last_24h_errors). Each card pairs a coloured status badge with an i18n
 * label so the colour signal is not the only cue (CLAUDE.md "Accessibility").
 *
 * The query polls every 30s. There is no manual mutation surface — operators
 * use the per-component pages (DT / Scans / Disk) for actions.
 */
import { AlertCircle, CheckCircle2, RefreshCw, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type HealthComponent,
  type HealthStatus,
} from "@/features/admin/health/api/adminHealthApi";
import { KevFeedPanel } from "@/features/admin/health/KevFeedPanel";
import { TrivyDBPanel } from "@/features/admin/health/TrivyDBPanel";
import { useAdminHealth } from "@/features/admin/health/api/useAdminHealth";
import { adminErrorMessageKey } from "@/features/admin/lib/adminErrorMessage";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

function statusVisuals(status: HealthStatus): {
  icon: typeof CheckCircle2;
  badge: string;
  ring: string;
} {
  if (status === "ok") {
    return {
      icon: CheckCircle2,
      badge: "border-emerald-300 bg-emerald-50 text-emerald-700",
      ring: "border-emerald-200",
    };
  }
  if (status === "degraded") {
    return {
      icon: ShieldAlert,
      badge: "border-amber-300 bg-amber-50 text-amber-800",
      ring: "border-amber-200",
    };
  }
  return {
    icon: AlertCircle,
    badge: "border-red-300 bg-red-50 text-red-700",
    ring: "border-red-200",
  };
}

function HealthCard({ component }: { component: HealthComponent }) {
  const { t } = useTranslation("admin");
  const visuals = statusVisuals(component.status);
  const Icon = visuals.icon;

  return (
    <article
      className={cn(
        "rounded-md border bg-card p-4 transition-colors duration-fast ease-out-soft",
        visuals.ring,
      )}
      data-testid="admin-health-card"
      data-component={component.name}
      data-status={component.status}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">
          {t(`admin.health.component.${component.name}`)}
        </h2>
        <Badge
          variant="outline"
          className={cn("gap-1 text-xs", visuals.badge)}
          data-testid="admin-health-status-badge"
          data-status={component.status}
        >
          <Icon className="h-3 w-3" aria-hidden />
          {t(`admin.health.status.${component.status}`)}
        </Badge>
      </div>
      <p
        className="text-xs text-muted-foreground"
        data-testid="admin-health-detail"
      >
        {component.detail ?? t("admin.health.no_detail")}
      </p>
      {component.value != null ? (
        <p
          className="mt-1 font-mono text-[11px] text-muted-foreground"
          data-testid="admin-health-value"
        >
          {component.value}
        </p>
      ) : null}
    </article>
  );
}

export function AdminHealthPage() {
  const { t, i18n } = useTranslation("admin");
  const healthQuery = useAdminHealth();

  const components = healthQuery.data?.components ?? [];

  return (
    <div className="flex h-full flex-col" data-testid="admin-health-page">
      <PageHeader
        title={t("admin.health.title")}
        description={t("admin.health.subtitle")}
        meta={
          healthQuery.data?.updated_at ? (
            <p
              className="text-xs text-muted-foreground"
              data-testid="admin-health-updated-at"
            >
              {t("admin.health.updated_at", {
                when: formatRelativeToNow(
                  healthQuery.data.updated_at,
                  i18n.resolvedLanguage,
                ),
              })}
            </p>
          ) : null
        }
        actions={
          <Button
            size="sm"
            variant="outline"
            onClick={() => healthQuery.refetch()}
            disabled={healthQuery.isFetching}
            data-testid="admin-health-refresh"
          >
            <RefreshCw
              className={cn("h-4 w-4", healthQuery.isFetching && "animate-spin")}
              aria-hidden
            />
            {t("admin.health.actions.refresh")}
          </Button>
        }
      />

      <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
        {healthQuery.isError ? (
          <Alert variant="destructive" data-testid="admin-health-error">
            <AlertDescription>
              {t(adminErrorMessageKey(healthQuery.error))}
            </AlertDescription>
          </Alert>
        ) : null}

        {/*
          W6-#43e: Trivy DB lifecycle panel + Phase C/C2: CISA KEV feed
          panel. Both sit above the probe grid so operators see the
          vulnerability-data state next to the other infrastructure health
          signals. Each panel owns its loading / error / empty state
          internally. Side-by-side on wide screens, stacked below xl.
        */}
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <TrivyDBPanel />
          <KevFeedPanel />
        </div>

        <div
          className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
          data-testid="admin-health-grid"
          aria-busy={healthQuery.isLoading}
        >
          {healthQuery.isLoading
            ? Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={`skeleton-${i}`}
                  className="rounded-md border bg-card p-4"
                  data-testid="admin-health-card-skeleton"
                >
                  <Skeleton className="mb-2 h-5 w-1/3" />
                  <Skeleton className="h-4 w-2/3" />
                </div>
              ))
            : components.map((component) => (
                <HealthCard key={component.name} component={component} />
              ))}
        </div>
      </div>
    </div>
  );
}
