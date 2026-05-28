import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

/**
 * RiskGauge — Phase 3 PR #10.
 *
 * Pure SVG semicircular gauge for the project Overview tab. Value is the
 * `risk_score` (0..100) computed by the backend
 * (`apps/backend/services/project_detail_service.py`).
 *
 * Why SVG and not recharts:
 *   - Zero new dependency.
 *   - No XSS surface — every value is rendered through React text nodes,
 *     never through `dangerouslySetInnerHTML`. The backend's score is
 *     numerically clamped 0..100 and stringified locally.
 *   - 60fps on a static drawing — no animation library needed.
 *
 * Color thresholds use the design tokens (var(--risk-*)) declared in
 * `index.css`; we never hardcode hex.
 *
 * W11-D (2026-05-28) — chart re-skin polish:
 *   - background arc bumped from `--muted` to `--border` so the empty track
 *     no longer disappears against the W11 muted card surface;
 *   - filled-arc length is animated via CSS `transition` on the
 *     `stroke-dasharray` over `--duration-base`, matching the Linear-polish
 *     motion vocabulary (the gauge fills smoothly when the underlying score
 *     updates after a scan);
 *   - severity threshold → token mapping unchanged (domain meaning fixed).
 */

const RADIUS = 70;
const STROKE = 12;
// Semicircle path length = π·r
const ARC_LENGTH = Math.PI * RADIUS;

function severityForScore(score: number): {
  token: string;
  i18nKey: string;
} {
  if (score >= 75) return { token: "var(--risk-critical)", i18nKey: "risk.critical" };
  if (score >= 50) return { token: "var(--risk-high)", i18nKey: "risk.high" };
  if (score >= 25) return { token: "var(--risk-medium)", i18nKey: "risk.medium" };
  if (score > 0) return { token: "var(--risk-low)", i18nKey: "risk.low" };
  return { token: "var(--risk-info)", i18nKey: "risk.none" };
}

/** Pixel + type-scale presets. The SVG viewBox is fixed (0 0 180 110) so the
 *  drawing simply scales to the chosen width/height; only the HTML number
 *  block needs its own type size + overlap offset per preset. */
const SIZES = {
  default: { w: 180, h: 110, valueClass: "text-3xl", offsetClass: "-mt-6" },
  sm: { w: 132, h: 80, valueClass: "text-xl", offsetClass: "-mt-4" },
} as const;

export interface RiskGaugeProps {
  /** 0..100 risk score from the project overview endpoint. */
  score: number;
  /** Visual size preset. `sm` is used for the side-by-side Security/License axes. */
  size?: keyof typeof SIZES;
  className?: string;
}

export function RiskGauge({ score, size = "default", className }: RiskGaugeProps) {
  const { t } = useTranslation("project_detail");

  // Clamp defensively even though the backend already enforces 0..100.
  const clamped = Math.max(0, Math.min(100, Number(score) || 0));
  const filled = (clamped / 100) * ARC_LENGTH;
  const severity = severityForScore(clamped);
  const dims = SIZES[size];

  return (
    <div
      data-testid="risk-gauge"
      data-score={clamped}
      className={cn("flex flex-col items-center justify-center", className)}
    >
      <svg
        viewBox="0 0 180 110"
        width={dims.w}
        height={dims.h}
        role="img"
        aria-label={t("overview.risk_gauge.aria", { score: clamped })}
      >
        {/* Background arc — `--border` reads as a quiet track against the
         *  W11 light card surface; `--muted` was almost invisible because the
         *  card itself sits on a `--muted/40` panel in several places. */}
        <path
          d={`M ${90 - RADIUS} 90 A ${RADIUS} ${RADIUS} 0 0 1 ${90 + RADIUS} 90`}
          fill="none"
          stroke="hsl(var(--border))"
          strokeWidth={STROKE}
          strokeLinecap="round"
        />
        {/* Filled arc — animate dasharray on score change (200 ms ease-out
         *  per `--duration-base`) so a fresh scan visibly fills the gauge. */}
        <path
          d={`M ${90 - RADIUS} 90 A ${RADIUS} ${RADIUS} 0 0 1 ${90 + RADIUS} 90`}
          fill="none"
          stroke={severity.token}
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={`${filled} ${ARC_LENGTH}`}
          style={{
            transition:
              "stroke-dasharray var(--duration-base) var(--ease-out), stroke var(--duration-base) var(--ease-out)",
          }}
        />
      </svg>
      <div className={cn(dims.offsetClass, "flex flex-col items-center")}>
        <span
          className={cn("font-semibold tabular-nums", dims.valueClass)}
          data-testid="risk-gauge-value"
        >
          {clamped.toFixed(0)}
          <span className="text-base font-normal text-muted-foreground">
            {" "}
            / 100
          </span>
        </span>
        <span
          className="text-xs uppercase tracking-wide text-muted-foreground"
          data-testid="risk-gauge-label"
        >
          {t(severity.i18nKey)}
        </span>
      </div>
    </div>
  );
}
