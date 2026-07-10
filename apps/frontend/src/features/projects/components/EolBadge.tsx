import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * EolBadge — endoflife.date end-of-life signal (Phase M).
 *
 * Structural mirror of {@link KevBadge}: a colored dot paired with the
 * literal "EOL" text label so color is never the only signal. Rendered ONLY
 * when the component's release cycle is past its published end-of-life
 * (`eol_state === "eol"`) — `supported`, `unknown` and untracked (`null`)
 * all render nothing, because absence already reads as "no signal" on the
 * dense 40px list and the tracked-product whitelist covers a tiny minority
 * of components.
 *
 * EOL is a maintenance-risk signal, not confirmed exploitation — one notch
 * below KEV — so the chip uses the High hue family (`high` Badge tone +
 * `bg-risk-high` dot), never Critical. It cannot be confused with the
 * severity column's chip: severity badges carry a status word while this
 * one carries the literal "EOL" label (the ReachabilityBadge rationale).
 *
 * The published end-of-life date rides the tooltip always, and inline on
 * `showDate` surfaces (drawer / detail). `data-eol-state` / `data-eol-date`
 * anchor e2e assertions so the spec never matches translated copy.
 */

export interface EolBadgeProps {
  /** The component's `eol_state` — anything but `"eol"` renders nothing. */
  eolState: string | null | undefined;
  /** Published EOL date (ISO, e.g. "2024-12-31") or `null` (boolean feeds). */
  eolDate?: string | null;
  /**
   * Render the date inline next to the label (drawer / detail surfaces).
   * Default `false`: list rows keep the chip narrow, date on tooltip only.
   */
  showDate?: boolean;
  className?: string;
}

export function EolBadge({
  eolState,
  eolDate,
  showDate = false,
  className,
}: EolBadgeProps) {
  const { t } = useTranslation("project_detail");

  if (eolState !== "eol") return null;

  const tooltip = eolDate
    ? t("components.eol.tooltip_with_date", { date: eolDate })
    : t("components.eol.tooltip");

  return (
    <Badge
      tone="high"
      data-testid="eol-badge"
      data-eol-state={eolState}
      data-eol-date={eolDate ?? undefined}
      title={tooltip}
      className={cn("gap-1.5 font-semibold", className)}
    >
      <span
        aria-hidden
        className="inline-block h-1.5 w-1.5 rounded-full bg-risk-high"
      />
      <span>{t("components.eol.label")}</span>
      {showDate && eolDate ? (
        <span
          className="font-mono text-[10px] font-normal tabular-nums"
          data-testid="eol-badge-date"
        >
          {t("components.eol.since", { date: eolDate })}
        </span>
      ) : null}
    </Badge>
  );
}
