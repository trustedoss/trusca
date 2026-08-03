// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { SlaStatus } from "@/features/projects/api/vulnerabilitiesApi";
import { cn } from "@/lib/utils";

/**
 * SlaBadge — remediation-SLA state of a vulnerability finding (X1 SLA/aging).
 *
 * Modeled on {@link KevBadge}: a colored dot paired with a literal state
 * label so color is never the only signal (CLAUDE.md "디자인 시스템" +
 * accessibility rule). Rendered ONLY when the finding carries an SLA —
 * `status === null` (info / unknown severity) renders nothing; on the dense
 * 40px list the caller shows its own em-dash placeholder for that case.
 *
 * ⚠️ The state is the SERVER-computed `sla_status` verbatim — this component
 * never recomputes it from `sla_due_date` (unlike KevBadge's client-side
 * {@link dueDateStatus} classification). Recomputing locally could disagree
 * with the backend's `?sla=` filter (clock skew, operator-tuned SLA windows)
 * and show an "ok" badge on a row the "overdue" filter just returned.
 *
 * Visual treatment per state:
 *
 *   - "overdue"  — solid critical fill (strongest treatment on the page,
 *                  same escalation KevBadge uses for a breached deadline).
 *   - "imminent" — amber tint (risk-medium + yellow-800, the AA-audited
 *                  Badge `medium` tone pair).
 *   - "ok"       — neutral low-emphasis tint (Badge `info` tone) so an
 *                  in-window finding stays quiet next to real signals.
 *
 * `showDueDate` renders the due DATE inline (drawer / detail surfaces);
 * list rows keep the chip narrow and carry the date on the tooltip only.
 * The `data-sla-status` anchor backs unit / e2e assertions.
 */

export interface SlaBadgeProps {
  /**
   * The finding's server-computed `sla_status`. `null` / `undefined`
   * (no SLA for this severity) renders nothing.
   */
  status: SlaStatus | null | undefined;
  /** SLA due instant (ISO-8601 datetime) or `null`. Display-only. */
  dueDate?: string | null;
  /**
   * Render the due date inline next to the state label (drawer / detail
   * surfaces). Default `false`: list rows carry the date via tooltip only.
   */
  showDueDate?: boolean;
  className?: string;
}

/**
 * State-specific chip treatment. Same layering strategy as KevBadge's
 * `dueVisuals`: state classes ride over the base tone via tailwind-merge,
 * and the dot always stays in the chip's hue family so the two agree.
 */
const SLA_VISUALS: Record<
  SlaStatus,
  { tone: "critical" | "medium" | "info"; badge?: string; dot: string }
> = {
  // Solid critical fill — white on `--risk-critical` (#dc2626) measures
  // 4.53:1, clearing WCAG AA (same audited pair as KevBadge overdue).
  overdue: {
    tone: "critical",
    badge: "bg-risk-critical text-white",
    dot: "bg-white",
  },
  // Amber — the Badge `medium` tone pair (risk-medium tint + yellow-800).
  imminent: { tone: "medium", dot: "bg-risk-medium" },
  // Neutral low-emphasis — Badge `info` tone (risk-info tint + slate-600).
  ok: { tone: "info", dot: "bg-risk-info" },
};

/**
 * `sla_due_date` arrives as a full ISO-8601 instant; the UI communicates the
 * deadline at DATE granularity (SLA windows are whole days). Defensive slice:
 * anything that doesn't look like an ISO datetime is shown verbatim.
 */
function dateOnly(iso: string): string {
  return /^\d{4}-\d{2}-\d{2}T/.test(iso) ? iso.slice(0, 10) : iso;
}

export function SlaBadge({
  status,
  dueDate,
  showDueDate = false,
  className,
}: SlaBadgeProps) {
  const { t } = useTranslation("project_detail");

  if (status == null) return null;

  const visuals = SLA_VISUALS[status];
  const date = dueDate != null ? dateOnly(dueDate) : null;

  const tooltip = date
    ? t("vulnerabilities.sla.tooltip_with_due", { date })
    : t("vulnerabilities.sla.tooltip");

  return (
    <Badge
      tone={visuals.tone}
      data-testid="sla-badge"
      data-sla-status={status}
      data-sla-due-date={date ?? undefined}
      title={tooltip}
      className={cn("gap-1.5 font-semibold", visuals.badge, className)}
    >
      <span
        aria-hidden
        className={cn("inline-block h-1.5 w-1.5 rounded-full", visuals.dot)}
      />
      <span>{t(`vulnerabilities.sla.state.${status}`)}</span>
      {showDueDate && date != null ? (
        <span
          className="font-mono text-[10px] font-normal tabular-nums"
          data-testid="sla-badge-due-date"
        >
          {t("vulnerabilities.sla.due", { date })}
        </span>
      ) : null}
    </Badge>
  );
}
