import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ScanStatus } from "@/lib/projectsApi";

/**
 * ProjectStatusBadge — Phase 2 PR #9 task 2.11.
 *
 * Maps a scan status to a tinted Badge plus a text label. CLAUDE.md's
 * accessibility rule "color is not the only signal" — every badge carries
 * a translated word in addition to the dot.
 */

export interface ProjectStatusBadgeProps {
  /** Scan status, or null when the project has never been scanned. */
  status: ScanStatus | "idle" | null;
  className?: string;
}

type Tone = "info" | "low" | "high" | "critical" | "success";

interface Visual {
  tone: Tone;
  i18nKey: string;
  testid: string;
}

function visualFor(status: ProjectStatusBadgeProps["status"]): Visual {
  switch (status) {
    case "queued":
      return { tone: "info", i18nKey: "status.queued", testid: "queued" };
    case "running":
      return { tone: "low", i18nKey: "status.running", testid: "running" };
    case "succeeded":
      return {
        tone: "success",
        i18nKey: "status.succeeded",
        testid: "succeeded",
      };
    case "failed":
      return { tone: "critical", i18nKey: "status.failed", testid: "failed" };
    case "cancelled":
      return { tone: "high", i18nKey: "status.failed", testid: "cancelled" };
    case "idle":
    case null:
    default:
      return { tone: "info", i18nKey: "status.idle", testid: "idle" };
  }
}

const DOT_COLOR_BY_TONE: Record<Tone, string> = {
  info: "bg-risk-info",
  low: "bg-risk-low",
  high: "bg-risk-high",
  critical: "bg-risk-critical",
  success: "bg-emerald-500",
};

export function ProjectStatusBadge({
  status,
  className,
}: ProjectStatusBadgeProps) {
  const { t } = useTranslation("projects");
  const visual = visualFor(status);
  return (
    <Badge
      tone={visual.tone}
      data-testid={`project-status-${visual.testid}`}
      data-status={status ?? "idle"}
      className={cn("gap-1.5", className)}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block h-1.5 w-1.5 rounded-full",
          DOT_COLOR_BY_TONE[visual.tone],
        )}
      />
      <span>{t(visual.i18nKey)}</span>
    </Badge>
  );
}
