import { useTranslation } from "react-i18next";

import type {
  ComponentSeverity,
  ProjectOverviewResponse,
} from "@/features/projects/api/projectDetailApi";
import { cn } from "@/lib/utils";

/**
 * SeverityDistributionChart — Phase 3 PR #10.
 *
 * Information-dense horizontal stacked bar + per-bucket legend. CLAUDE.md
 * "디자인 시스템" prefers compact, dense panels over dramatic donuts.
 *
 * Built with pure CSS / div layout (flex). No recharts, no SVG ops, no
 * `dangerouslySetInnerHTML` — every count is rendered through a React text
 * node so there is zero XSS surface even when an API regression returns a
 * stringy / weird value.
 */

const ORDERED_BUCKETS: ComponentSeverity[] = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
  "none",
];

const COLOR_BY_BUCKET: Record<ComponentSeverity, string> = {
  critical: "bg-risk-critical",
  high: "bg-risk-high",
  medium: "bg-risk-medium",
  low: "bg-risk-low",
  info: "bg-risk-info",
  none: "bg-muted-foreground/30",
};

export interface SeverityDistributionChartProps {
  distribution: ProjectOverviewResponse["severity_distribution"];
  className?: string;
}

export function SeverityDistributionChart({
  distribution,
  className,
}: SeverityDistributionChartProps) {
  const { t } = useTranslation("project_detail");
  const counts: Record<ComponentSeverity, number> = {
    critical: distribution.critical ?? 0,
    high: distribution.high ?? 0,
    medium: distribution.medium ?? 0,
    low: distribution.low ?? 0,
    info: distribution.info ?? 0,
    none: distribution.none ?? 0,
  };
  const total = ORDERED_BUCKETS.reduce((sum, key) => sum + counts[key], 0);

  return (
    <div
      data-testid="severity-distribution-chart"
      data-total={total}
      className={cn("flex flex-col gap-3", className)}
    >
      <div
        className="flex h-3 w-full overflow-hidden rounded-md bg-muted"
        role="img"
        aria-label={t("overview.severity_chart.aria", { total })}
      >
        {total > 0
          ? ORDERED_BUCKETS.map((key) => {
              const count = counts[key];
              if (count <= 0) return null;
              const pct = (count / total) * 100;
              return (
                <div
                  key={key}
                  data-testid={`severity-bar-${key}`}
                  data-severity={key}
                  data-count={count}
                  className={cn("h-full", COLOR_BY_BUCKET[key])}
                  style={{ width: `${pct}%` }}
                  title={`${t(`severity.${key}`)}: ${count}`}
                />
              );
            })
          : null}
      </div>
      <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
        {ORDERED_BUCKETS.map((key) => (
          <li
            key={key}
            data-testid={`severity-legend-${key}`}
            className="flex items-center gap-2"
          >
            <span
              aria-hidden
              className={cn(
                "inline-block h-2 w-2 rounded-full",
                COLOR_BY_BUCKET[key],
              )}
            />
            <span className="text-muted-foreground">
              {t(`severity.${key}`)}
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
