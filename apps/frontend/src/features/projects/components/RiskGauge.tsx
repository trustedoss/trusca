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

export interface RiskGaugeProps {
  /** 0..100 risk score from the project overview endpoint. */
  score: number;
  className?: string;
}

export function RiskGauge({ score, className }: RiskGaugeProps) {
  const { t } = useTranslation("project_detail");

  // Clamp defensively even though the backend already enforces 0..100.
  const clamped = Math.max(0, Math.min(100, Number(score) || 0));
  const filled = (clamped / 100) * ARC_LENGTH;
  const severity = severityForScore(clamped);

  return (
    <div
      data-testid="risk-gauge"
      data-score={clamped}
      className={cn("flex flex-col items-center justify-center", className)}
    >
      <svg
        viewBox="0 0 180 110"
        width="180"
        height="110"
        role="img"
        aria-label={t("overview.risk_gauge.aria", { score: clamped })}
      >
        {/* Background arc */}
        <path
          d={`M ${90 - RADIUS} 90 A ${RADIUS} ${RADIUS} 0 0 1 ${90 + RADIUS} 90`}
          fill="none"
          stroke="hsl(var(--muted))"
          strokeWidth={STROKE}
          strokeLinecap="round"
        />
        {/* Filled arc */}
        <path
          d={`M ${90 - RADIUS} 90 A ${RADIUS} ${RADIUS} 0 0 1 ${90 + RADIUS} 90`}
          fill="none"
          stroke={severity.token}
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={`${filled} ${ARC_LENGTH}`}
        />
      </svg>
      <div className="-mt-6 flex flex-col items-center">
        <span
          className="text-3xl font-semibold tabular-nums"
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
