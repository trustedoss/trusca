import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { dueDateStatus, type DueDateStatus } from "@/lib/dueDate";
import { cn } from "@/lib/utils";

/**
 * KevBadge — CISA KEV (Known Exploited Vulnerabilities) signal.
 *
 * Modeled on {@link ReachabilityBadge}: a colored dot paired with the literal
 * "KEV" text label so color is never the only signal (CLAUDE.md "디자인 시스템"
 * + accessibility rule). Rendered ONLY when the CVE is catalog-listed —
 * absence already reads as "no signal" on the dense 40px list, and a
 * "not listed" chip would be noise everywhere (the catalog covers a tiny
 * minority of CVEs).
 *
 * KEV membership means exploitation in the wild is CONFIRMED — the strongest
 * triage signal we surface — so the badge uses the Critical hue family
 * (`critical` Badge tone + `bg-risk-critical` dot). It never reads as the
 * severity column's chip because severity badges carry a status word
 * (Critical / High / …) while this one carries the literal "KEV" label —
 * the same disambiguation rationale ReachabilityBadge documents for orange.
 *
 * Phase C / C3 — remediation-deadline (SLA) visualization. When a due date
 * is present the badge escalates by {@link dueDateStatus}:
 *
 *   - "overdue"  — solid critical fill (strongest treatment on the page) +
 *                  inline "Overdue by n days" on `showDueDate` surfaces.
 *   - "imminent" — amber (risk-medium) tint + inline "Due in n days" /
 *                  "Due today".
 *   - "ok"       — unchanged from the pre-C3 badge: critical tint + muted
 *                  "Due {date}" inline text.
 *
 * List rows (`showDueDate={false}`) convey the state through the badge tone
 * alone to keep the 40px row quiet; the full absolute date always rides on
 * the tooltip, the "KEV" text label is always present, and `data-due-state`
 * anchors e2e assertions — so color is never the only carrier of meaning.
 */

export interface KevBadgeProps {
  /** The CVE's `kev` field — `false`/`undefined` renders nothing. */
  kev: boolean | undefined;
  /** CISA remediation due date (ISO date, e.g. "2026-07-15") or `null`. */
  dueDate?: string | null;
  /**
   * Render the due date inline next to the label (drawer / detail surfaces).
   * Default `false`: list rows keep the chip narrow and carry the date on
   * the tooltip only.
   */
  showDueDate?: boolean;
  /** Injectable clock for deterministic SLA classification in tests. */
  now?: Date;
  className?: string;
}

/**
 * State-specific chip treatment layered over the base `tone="critical"`
 * classes (tailwind-merge dedupes the bg-/text- pairs). The dot keeps the
 * same hue family as the chip so the two never disagree.
 */
function dueVisuals(state: DueDateStatus["state"] | null): {
  badge?: string;
  dot: string;
} {
  if (state === "overdue") {
    // Solid critical fill — the escalation above the default tint. White on
    // `--risk-critical` (#dc2626) measures 4.53:1, clearing WCAG AA.
    return { badge: "bg-risk-critical text-white", dot: "bg-white" };
  }
  if (state === "imminent") {
    // Amber — mirrors the Badge `medium` tone pair (risk-medium tint +
    // yellow-800 text, the AA-audited combination from W11-H).
    return {
      badge: "bg-risk-medium/15 text-yellow-800",
      dot: "bg-risk-medium",
    };
  }
  // "ok" or no due date — pre-C3 appearance.
  return { dot: "bg-risk-critical" };
}

export function KevBadge({
  kev,
  dueDate,
  showDueDate = false,
  now,
  className,
}: KevBadgeProps) {
  const { t } = useTranslation("project_detail");

  if (kev !== true) return null;

  const due = dueDate ? dueDateStatus(dueDate, now) : null;
  // Malformed date strings degrade to the pre-C3 badge (no SLA state).
  const dueState = due && Number.isFinite(due.days) ? due.state : null;
  const visuals = dueVisuals(dueState);

  const tooltip = dueDate
    ? t("vulnerabilities.kev.tooltip_with_due", { date: dueDate })
    : t("vulnerabilities.kev.tooltip");

  let dueText: string | null = null;
  if (showDueDate && dueDate) {
    if (dueState === "overdue" && due) {
      dueText = t("vulnerabilities.kev.due_overdue", {
        days: Math.abs(due.days),
      });
    } else if (dueState === "imminent" && due) {
      dueText =
        due.days === 0
          ? t("vulnerabilities.kev.due_today")
          : t("vulnerabilities.kev.due_imminent", { days: due.days });
    } else {
      dueText = t("vulnerabilities.kev.due", { date: dueDate });
    }
  }

  return (
    <Badge
      tone="critical"
      data-testid="kev-badge"
      data-kev-due-date={dueDate ?? undefined}
      data-due-state={dueState ?? undefined}
      title={tooltip}
      className={cn("gap-1.5 font-semibold", visuals.badge, className)}
    >
      <span
        aria-hidden
        className={cn("inline-block h-1.5 w-1.5 rounded-full", visuals.dot)}
      />
      <span>{t("vulnerabilities.kev.label")}</span>
      {dueText != null ? (
        <span
          className="font-mono text-[10px] font-normal tabular-nums"
          data-testid="kev-badge-due-date"
        >
          {dueText}
        </span>
      ) : null}
    </Badge>
  );
}
