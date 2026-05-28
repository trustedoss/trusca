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
 *
 * W11-D (2026-05-28) — chart re-skin polish:
 *   - track tint softened to `bg-muted/70` + 1 px inset ring on `border` for
 *     a quieter container on the Vercel-light canvas;
 *   - segment hover transition unified at 150 ms (matches `--duration-fast`);
 *   - legend dot is now ring-bordered so the 2 px chip reads even when the
 *     adjacent risk colour is very light (info / unknown);
 *   - risk severity colour mapping unchanged (domain meaning is fixed —
 *     critical / high / medium / low / info).
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
  /**
   * W4-B-prep: optional click handler for chart segments + legend entries.
   * When provided, both the stacked-bar segment and the matching legend row
   * become buttons; the caller decides what the click means (typically a
   * deep-link such as `?tab=vulnerabilities&severity=critical`).
   *
   * Backwards-compatible: when omitted, the chart renders the same static
   * shape it did before (div segments + non-interactive legend rows). Buckets
   * with `count === 0` stay non-interactive even when the callback is set —
   * there's nothing to filter to.
   */
  onSegmentClick?: (key: ComponentSeverity) => void;
  /**
   * Override the legend label for the trailing bucket. The chart's last
   * slot is keyed as ``none`` to keep the layout/colour stable, but in the
   * Vulnerabilities tab that same slot actually counts finding-level
   * ``unknown`` severities. Pass ``"Unknown"`` (or its localized form) to
   * relabel without forking the component. Falls back to the existing
   * ``severity.none`` translation when omitted.
   */
  noneLabel?: string;
  className?: string;
}

export function SeverityDistributionChart({
  distribution,
  onSegmentClick,
  noneLabel,
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
  const interactive = onSegmentClick !== undefined;

  return (
    <div
      data-testid="severity-distribution-chart"
      data-total={total}
      className={cn("flex flex-col gap-3", className)}
    >
      <div
        className="flex h-3 w-full overflow-hidden rounded-md bg-muted/70 ring-1 ring-inset ring-border/60"
        role="img"
        aria-label={t("overview.severity_chart.aria", { total })}
      >
        {total > 0
          ? ORDERED_BUCKETS.map((key) => {
              const count = counts[key];
              if (count <= 0) return null;
              const pct = (count / total) * 100;
              const segmentClass = cn("h-full", COLOR_BY_BUCKET[key]);
              const labelText =
                key === "none" && noneLabel != null
                  ? noneLabel
                  : t(`severity.${key}`);
              const title = `${labelText}: ${count}`;
              if (interactive) {
                return (
                  <button
                    type="button"
                    key={key}
                    data-testid={`severity-bar-${key}`}
                    data-severity={key}
                    data-count={count}
                    className={cn(
                      segmentClass,
                      "transition-opacity duration-150 ease-out hover:opacity-80 focus-visible:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
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
                  data-testid={`severity-bar-${key}`}
                  data-severity={key}
                  data-count={count}
                  className={segmentClass}
                  style={{ width: `${pct}%` }}
                  title={title}
                />
              );
            })
          : null}
      </div>
      <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
        {ORDERED_BUCKETS.map((key) => {
          const count = counts[key];
          const dotAndLabel = (
            <>
              <span
                aria-hidden
                className={cn(
                  "inline-block h-2 w-2 shrink-0 rounded-full ring-1 ring-inset ring-border/40",
                  COLOR_BY_BUCKET[key],
                )}
              />
              <span className="text-muted-foreground">
                {key === "none" && noneLabel != null
                  ? noneLabel
                  : t(`severity.${key}`)}
              </span>
              <span className="ml-auto font-medium tabular-nums">{count}</span>
            </>
          );
          if (interactive && count > 0) {
            return (
              <li key={key}>
                <button
                  type="button"
                  data-testid={`severity-legend-${key}`}
                  className={cn(
                    "flex w-full items-center gap-2 rounded px-1 py-0.5 text-left",
                    "transition-colors duration-150 ease-out hover:bg-accent hover:text-accent-foreground",
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
              data-testid={`severity-legend-${key}`}
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
