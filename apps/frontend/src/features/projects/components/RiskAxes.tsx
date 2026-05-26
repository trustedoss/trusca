import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";
import type {
  ComponentSeverity,
  LicenseCategoryName,
} from "@/features/projects/api/projectDetailApi";

import { RiskGauge } from "./RiskGauge";

/**
 * RiskAxes — Wave 1 #34.
 *
 * Replaces the single composite RiskGauge on the project Overview with two
 * independent axes: Security (worst CVE severity) and License (worst license
 * category). The old single score conflated the two, so a project with zero
 * vulnerabilities but a handful of `conditional` licenses rendered as
 * "Critical 100". Each axis is now scored separately and non-saturating
 * (see `services/risk_score.py`), and the caption surfaces the driving counts
 * so the grade is explainable at a glance.
 *
 * Both gauges use the small `RiskGauge` preset so they sit side by side inside
 * the half-width Overview card.
 */

export interface RiskAxesProps {
  securityScore: number;
  licenseScore: number;
  severityDistribution: Partial<Record<ComponentSeverity, number>>;
  licenseDistribution: Partial<Record<LicenseCategoryName, number>>;
  className?: string;
}

export function RiskAxes({
  securityScore,
  licenseScore,
  severityDistribution,
  licenseDistribution,
  className,
}: RiskAxesProps) {
  const { t } = useTranslation("project_detail");

  const critical = severityDistribution.critical ?? 0;
  const high = severityDistribution.high ?? 0;
  const forbidden = licenseDistribution.forbidden ?? 0;
  const conditional = licenseDistribution.conditional ?? 0;

  return (
    <div
      data-testid="risk-axes"
      className={cn("flex w-full items-start justify-around gap-4", className)}
    >
      <div
        data-testid="risk-axis-security"
        data-score={securityScore}
        className="flex flex-1 flex-col items-center"
      >
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {t("overview.risk_card.security")}
        </span>
        <RiskGauge score={securityScore} size="sm" />
        <span
          data-testid="risk-axis-security-counts"
          className="mt-1 text-center text-xs text-muted-foreground"
        >
          {t("overview.risk_card.security_counts", { critical, high })}
        </span>
      </div>

      <div
        data-testid="risk-axis-license"
        data-score={licenseScore}
        className="flex flex-1 flex-col items-center"
      >
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {t("overview.risk_card.license")}
        </span>
        <RiskGauge score={licenseScore} size="sm" />
        <span
          data-testid="risk-axis-license-counts"
          className="mt-1 text-center text-xs text-muted-foreground"
        >
          {t("overview.risk_card.license_counts", { forbidden, conditional })}
        </span>
      </div>
    </div>
  );
}
