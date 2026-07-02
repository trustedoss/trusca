import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
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
 * The CISA remediation due date, when present, rides on the tooltip in every
 * surface; the drawer additionally renders it inline (`showDueDate`) so the
 * deadline is legible up close without hovering.
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
  className?: string;
}

export function KevBadge({
  kev,
  dueDate,
  showDueDate = false,
  className,
}: KevBadgeProps) {
  const { t } = useTranslation("project_detail");

  if (kev !== true) return null;

  const tooltip = dueDate
    ? t("vulnerabilities.kev.tooltip_with_due", { date: dueDate })
    : t("vulnerabilities.kev.tooltip");

  return (
    <Badge
      tone="critical"
      data-testid="kev-badge"
      data-kev-due-date={dueDate ?? undefined}
      title={tooltip}
      className={cn("gap-1.5 font-semibold", className)}
    >
      <span
        aria-hidden
        className="inline-block h-1.5 w-1.5 rounded-full bg-risk-critical"
      />
      <span>{t("vulnerabilities.kev.label")}</span>
      {showDueDate && dueDate ? (
        <span
          className="font-mono text-[10px] font-normal tabular-nums"
          data-testid="kev-badge-due-date"
        >
          {t("vulnerabilities.kev.due", { date: dueDate })}
        </span>
      ) : null}
    </Badge>
  );
}
