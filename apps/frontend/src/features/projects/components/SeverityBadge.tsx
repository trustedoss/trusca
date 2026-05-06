import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { ComponentSeverity } from "@/features/projects/api/projectDetailApi";
import { cn } from "@/lib/utils";

/**
 * SeverityBadge — Phase 3 PR #10.
 *
 * Pairs a risk-tinted dot with the localized severity label so color is never
 * the only signal (CLAUDE.md "디자인 시스템" + accessibility rule). Maps the
 * backend's six-bucket severity (critical/high/medium/low/info/none) onto the
 * Badge `tone` variants in `components/ui/badge.tsx`.
 */
type Tone = "critical" | "high" | "medium" | "low" | "info";

interface Visual {
  tone: Tone;
  dot: string;
}

const VISUAL_BY_SEVERITY: Record<ComponentSeverity, Visual> = {
  critical: { tone: "critical", dot: "bg-risk-critical" },
  high: { tone: "high", dot: "bg-risk-high" },
  medium: { tone: "medium", dot: "bg-risk-medium" },
  low: { tone: "low", dot: "bg-risk-low" },
  info: { tone: "info", dot: "bg-risk-info" },
  none: { tone: "info", dot: "bg-risk-info" },
};

export interface SeverityBadgeProps {
  severity: ComponentSeverity;
  className?: string;
}

export function SeverityBadge({ severity, className }: SeverityBadgeProps) {
  const { t } = useTranslation("project_detail");
  const visual = VISUAL_BY_SEVERITY[severity];
  return (
    <Badge
      tone={visual.tone}
      data-testid={`severity-badge-${severity}`}
      data-severity={severity}
      className={cn("gap-1.5", className)}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block h-1.5 w-1.5 rounded-full",
          visual.dot,
        )}
      />
      <span>{t(`severity.${severity}`)}</span>
    </Badge>
  );
}
