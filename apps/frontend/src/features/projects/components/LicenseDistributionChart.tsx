import { useTranslation } from "react-i18next";

import type {
  LicenseCategoryName,
  ProjectOverviewResponse,
} from "@/features/projects/api/projectDetailApi";
import { cn } from "@/lib/utils";

/**
 * LicenseDistributionChart — Phase 3 PR #10.
 *
 * Stacked horizontal bar with category counts inline. forbidden = critical
 * red, conditional = amber, allowed = emerald, unknown = gray. Pure CSS, no
 * recharts (no XSS surface).
 */

const ORDER: LicenseCategoryName[] = [
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
];

const COLOR: Record<LicenseCategoryName, string> = {
  forbidden: "bg-risk-critical",
  conditional: "bg-risk-medium",
  allowed: "bg-emerald-500",
  unknown: "bg-risk-info",
};

export interface LicenseDistributionChartProps {
  distribution: ProjectOverviewResponse["license_distribution"];
  className?: string;
}

export function LicenseDistributionChart({
  distribution,
  className,
}: LicenseDistributionChartProps) {
  const { t } = useTranslation("project_detail");
  const counts: Record<LicenseCategoryName, number> = {
    forbidden: distribution.forbidden ?? 0,
    conditional: distribution.conditional ?? 0,
    allowed: distribution.allowed ?? 0,
    unknown: distribution.unknown ?? 0,
  };
  const total = ORDER.reduce((sum, key) => sum + counts[key], 0);

  return (
    <div
      data-testid="license-distribution-chart"
      data-total={total}
      className={cn("flex flex-col gap-3", className)}
    >
      <div
        className="flex h-3 w-full overflow-hidden rounded-md bg-muted"
        role="img"
        aria-label={t("overview.license_chart.aria", { total })}
      >
        {total > 0
          ? ORDER.map((key) => {
              const count = counts[key];
              if (count <= 0) return null;
              const pct = (count / total) * 100;
              return (
                <div
                  key={key}
                  data-testid={`license-bar-${key}`}
                  data-license-category={key}
                  data-count={count}
                  className={cn("h-full", COLOR[key])}
                  style={{ width: `${pct}%` }}
                  title={`${t(`license_category.${key}`)}: ${count}`}
                />
              );
            })
          : null}
      </div>
      <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
        {ORDER.map((key) => (
          <li
            key={key}
            data-testid={`license-legend-${key}`}
            className="flex items-center gap-2"
          >
            <span
              aria-hidden
              className={cn("inline-block h-2 w-2 rounded-full", COLOR[key])}
            />
            <span className="text-muted-foreground">
              {t(`license_category.${key}`)}
            </span>
            <span className="ml-auto font-medium tabular-nums">
              {counts[key]}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
