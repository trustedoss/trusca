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
  /**
   * W4-B-prep: optional click handler for chart segments + legend entries.
   * When provided, both the stacked-bar segment and the matching legend row
   * become buttons; the caller decides what the click means (typically a
   * deep-link such as `?tab=licenses&category=forbidden`).
   *
   * Backwards-compatible: when omitted, the chart renders the same static
   * shape it did before (div segments + non-interactive legend rows). Buckets
   * with `count === 0` stay non-interactive even when the callback is set —
   * there's nothing to filter to.
   */
  onSegmentClick?: (key: LicenseCategoryName) => void;
  className?: string;
}

export function LicenseDistributionChart({
  distribution,
  onSegmentClick,
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
  const interactive = onSegmentClick !== undefined;

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
              const segmentClass = cn("h-full", COLOR[key]);
              const title = `${t(`license_category.${key}`)}: ${count}`;
              if (interactive) {
                return (
                  <button
                    type="button"
                    key={key}
                    data-testid={`license-bar-${key}`}
                    data-license-category={key}
                    data-count={count}
                    className={cn(
                      segmentClass,
                      "transition-opacity hover:opacity-80 focus-visible:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    )}
                    style={{ width: `${pct}%` }}
                    title={title}
                    aria-label={title}
                    onClick={() => onSegmentClick(key)}
                  />
                );
              }
              return (
                <div
                  key={key}
                  data-testid={`license-bar-${key}`}
                  data-license-category={key}
                  data-count={count}
                  className={segmentClass}
                  style={{ width: `${pct}%` }}
                  title={title}
                />
              );
            })
          : null}
      </div>
      {/* 4 categories on a single line at sm+ (grid-cols-4) overflowed the
          Overview card on a ~700-800px main pane — the labels visually
          collided ("0Conditional", "0Allowed"). Keep the legend at 2 columns
          everywhere so the row breaks into a stable 2×2 instead. Severity's
          legend stays at 3 cols because it has six buckets and the third
          column matters; license's four buckets read better stacked 2×2. */}
      <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        {ORDER.map((key) => {
          const count = counts[key];
          const dotAndLabel = (
            <>
              <span
                aria-hidden
                className={cn("inline-block h-2 w-2 rounded-full", COLOR[key])}
              />
              <span className="text-muted-foreground">
                {t(`license_category.${key}`)}
              </span>
              <span className="ml-auto font-medium tabular-nums">{count}</span>
            </>
          );
          if (interactive && count > 0) {
            return (
              <li key={key}>
                <button
                  type="button"
                  data-testid={`license-legend-${key}`}
                  className={cn(
                    "flex w-full items-center gap-2 rounded px-1 py-0.5 text-left",
                    "hover:bg-accent hover:text-accent-foreground",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                  )}
                  onClick={() => onSegmentClick(key)}
                >
                  {dotAndLabel}
                </button>
              </li>
            );
          }
          return (
            <li
              key={key}
              data-testid={`license-legend-${key}`}
              className="flex items-center gap-2"
            >
              {dotAndLabel}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
