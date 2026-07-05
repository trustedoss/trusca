import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { ReviewFlag } from "@/features/projects/api/licensesApi";
import { cn } from "@/lib/utils";

/**
 * ReviewFlagBadge — AI license review flag (Phase D).
 *
 * Surfaces the two AI-relevant restriction classes the scan pipeline flags for
 * human review (behavioral-use RAIL/community licenses; non-commercial terms).
 * Rendered as an amber "Review needed" chip so it lines up with the medium
 * (amber) risk tone the rest of the app uses for "legal review required".
 *
 * Accessibility: color is never the only signal — the badge pairs the amber
 * tone with a warning icon AND the localized "Review needed" word, and a
 * `title` tooltip plus the visible short label distinguish the two classes.
 * The portal only surfaces the class; whether the restriction actually applies
 * is a human/legal judgement.
 */
export interface ReviewFlagBadgeProps {
  flag: ReviewFlag;
  /** Show the flag-specific short label (Behavioral-use / Non-commercial). */
  showKind?: boolean;
  className?: string;
}

export function ReviewFlagBadge({
  flag,
  showKind = false,
  className,
}: ReviewFlagBadgeProps) {
  const { t } = useTranslation("project_detail");
  return (
    <Badge
      tone="medium"
      data-testid="license-review-flag-badge"
      data-review-flag={flag}
      title={t(`licenses.review.description.${flag}`)}
      className={cn("gap-1", className)}
    >
      <AlertTriangle className="h-3 w-3" aria-hidden />
      <span>{t("licenses.review.badge_label")}</span>
      {showKind ? (
        <span className="text-[11px] font-normal opacity-80">
          · {t(`licenses.review.short.${flag}`)}
        </span>
      ) : null}
    </Badge>
  );
}
