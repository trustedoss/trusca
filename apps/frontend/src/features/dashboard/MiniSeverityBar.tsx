/**
 * MiniSeverityBar — small KPI-card decoration used by DashboardPage.
 *
 * Renders a single-row severity dot strip (C / H / M / L) so the
 * open-vulns KPI carries an at-a-glance breakdown without taking another
 * row of vertical space. Lives in its own file so the i18next-parser can
 * unambiguously route its `useTranslation("projects")` keys to the
 * `projects` namespace (DashboardPage owns the `dashboard` namespace —
 * mixing them in one file confuses the static analyzer).
 */
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

export interface SeverityCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
  none: number;
}

export function MiniSeverityBar({ counts }: { counts: SeverityCounts }) {
  const { t } = useTranslation("projects");
  const buckets: Array<{
    key: keyof SeverityCounts;
    count: number;
    colorClass: string;
    abbrevKey: string;
  }> = [
    {
      key: "critical",
      count: counts.critical,
      colorClass: "text-risk-critical",
      abbrevKey: "severity.abbrev.critical",
    },
    {
      key: "high",
      count: counts.high,
      colorClass: "text-risk-high",
      abbrevKey: "severity.abbrev.high",
    },
    {
      key: "medium",
      count: counts.medium,
      colorClass: "text-risk-medium",
      abbrevKey: "severity.abbrev.medium",
    },
    {
      key: "low",
      count: counts.low,
      colorClass: "text-risk-low",
      abbrevKey: "severity.abbrev.low",
    },
  ];
  const present = buckets.filter((b) => b.count > 0);
  if (present.length === 0) return null;
  return (
    <div
      className="flex items-center gap-2 font-mono text-xs"
      data-testid="dashboard-mini-severity"
    >
      {present.map((bucket, idx) => (
        <span key={bucket.key} className="flex items-center gap-2">
          {idx > 0 ? (
            <span aria-hidden className="text-muted-foreground">
              ·
            </span>
          ) : null}
          <span className={cn("font-medium", bucket.colorClass)}>
            <span aria-hidden>{t(bucket.abbrevKey)}</span> {bucket.count}
          </span>
        </span>
      ))}
    </div>
  );
}
