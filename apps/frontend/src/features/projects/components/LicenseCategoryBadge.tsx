import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";
import { cn } from "@/lib/utils";

/**
 * LicenseCategoryBadge — Phase 3 PR #10.
 *
 * forbidden  → critical tone (red)    — build-blocking license
 * conditional → medium tone (amber)   — legal review required
 * allowed    → info tone (gray)       — permissive
 * unknown    → info tone (gray)
 *
 * Color always paired with localized text so it remains accessible.
 */
type Tone = "critical" | "medium" | "info";

const TONE_BY_CATEGORY: Record<LicenseCategoryName, Tone> = {
  forbidden: "critical",
  conditional: "medium",
  allowed: "info",
  unknown: "info",
};

const DOT_BY_CATEGORY: Record<LicenseCategoryName, string> = {
  forbidden: "bg-risk-critical",
  conditional: "bg-risk-medium",
  allowed: "bg-emerald-500",
  unknown: "bg-risk-info",
};

export interface LicenseCategoryBadgeProps {
  category: LicenseCategoryName;
  className?: string;
}

export function LicenseCategoryBadge({
  category,
  className,
}: LicenseCategoryBadgeProps) {
  const { t } = useTranslation("project_detail");
  return (
    <Badge
      tone={TONE_BY_CATEGORY[category]}
      data-testid={`license-category-badge-${category}`}
      data-license-category={category}
      className={cn("gap-1.5", className)}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block h-1.5 w-1.5 rounded-full",
          DOT_BY_CATEGORY[category],
        )}
      />
      <span>{t(`license_category.${category}`)}</span>
    </Badge>
  );
}
