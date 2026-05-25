import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * ReachabilityBadge — v2.3 r2.
 *
 * Tri-state reachability signal for a vulnerability finding, modeled on
 * {@link SeverityBadge}: a colored dot paired with a localized label so color
 * is never the only signal (CLAUDE.md "디자인 시스템" + accessibility rule).
 *
 *   - `true`  → "Reachable": the vulnerable symbol sits on the project's call
 *               graph. This is a *priority* signal — a reachable Critical is far
 *               more urgent than a present-but-dead one — so it gets the loud
 *               `high` (warning-orange) tone and is visually prominent.
 *   - `false` → "Not reachable": an analyser ran and proved the symbol is dead
 *               code for this project. A calm, de-emphasised `muted` chip.
 *   - `null`  → "Not analysed": no reachability run touched this finding (or its
 *               ecosystem is out of the analyser's scope). To avoid noise on the
 *               dense 40px list, the list surface renders nothing for this state
 *               (`compact` mode); the drawer renders an explicit muted chip so
 *               the absence of analysis is legible up close.
 *
 * The `high` tone is deliberately distinct from the severity column's own tones:
 * severity badges always carry a status WORD (Critical / High / …) plus a dot,
 * whereas this badge carries the literal "Reachable" label, so the two never
 * read as the same chip even when both happen to be orange.
 */

/** The three rendered states keyed off the wire's `boolean | null`. */
export type ReachabilityState = "reachable" | "unreachable" | "unknown";

type Tone = "high" | "none";

interface Visual {
  /** Badge tone variant from `components/ui/badge.tsx`. */
  tone: Tone;
  /** Extra classes layered on the Badge (muted chip styling for non-loud states). */
  className: string;
  /** Risk-token dot color class, or `null` to omit the dot. */
  dot: string | null;
}

const VISUAL_BY_STATE: Record<ReachabilityState, Visual> = {
  // Loud, risk-forward: reachable findings jump the triage queue.
  reachable: {
    tone: "high",
    className: "font-semibold",
    dot: "bg-risk-high",
  },
  // Calm: analysed-and-dead. Subtle muted chip, neutral dot.
  unreachable: {
    tone: "none",
    className: "border-input bg-muted text-slate-600 dark:text-slate-300",
    dot: "bg-risk-info",
  },
  // Neutral: not analysed. Only shown in non-compact (drawer) surfaces.
  unknown: {
    tone: "none",
    className:
      "border-dashed border-input bg-transparent text-muted-foreground",
    dot: null,
  },
};

/** Map the wire's `boolean | null` onto a render state. */
export function reachabilityState(value: boolean | null): ReachabilityState {
  if (value === true) return "reachable";
  if (value === false) return "unreachable";
  return "unknown";
}

export interface ReachabilityBadgeProps {
  /** The finding's `reachable` field (tri-state). */
  reachable: boolean | null;
  /**
   * Compact mode (default): render nothing for the not-analysed (`null`) state
   * so the dense list doesn't fill with neutral "Not analysed" noise. The drawer
   * passes `compact={false}` to always show an explicit chip.
   */
  compact?: boolean;
  /** Analyser identifier ("govulncheck") — folded into the title tooltip. */
  source?: string | null;
  className?: string;
}

export function ReachabilityBadge({
  reachable,
  compact = true,
  source,
  className,
}: ReachabilityBadgeProps) {
  const { t } = useTranslation("project_detail");
  const state = reachabilityState(reachable);

  // In compact (list) mode, the not-analysed state is rendered as nothing to
  // keep the 40px row quiet — absence already reads as "no signal".
  if (compact && state === "unknown") return null;

  const visual = VISUAL_BY_STATE[state];
  const label = t(`vulnerabilities.reachability.label.${state}`);
  const tooltip =
    source && state !== "unknown"
      ? t("vulnerabilities.reachability.tooltip_with_source", {
          label,
          source,
        })
      : t(`vulnerabilities.reachability.tooltip.${state}`);

  return (
    <Badge
      tone={visual.tone}
      data-testid={`reachability-badge-${state}`}
      data-reachability={state}
      title={tooltip}
      className={cn("gap-1.5", visual.className, className)}
    >
      {visual.dot ? (
        <span
          aria-hidden
          className={cn("inline-block h-1.5 w-1.5 rounded-full", visual.dot)}
        />
      ) : null}
      <span>{label}</span>
    </Badge>
  );
}
