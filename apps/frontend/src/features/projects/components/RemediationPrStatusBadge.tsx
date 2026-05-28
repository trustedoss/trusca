/**
 * RemediationPrStatusBadge — v2.2 Track B (b3 frontend).
 *
 * Renders a remediation-PR status (creating | open | failed | superseded) as a
 * tinted Badge. Per CLAUDE.md "color is not the only signal" — every state
 * pairs a design-token tone with a translated text label, so a colorblind user
 * still reads the status word.
 */
import { useTranslation } from "react-i18next";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import type { RemediationPRStatus } from "@/lib/remediationApi";

interface Props {
  status: RemediationPRStatus;
}

const TONE_BY_STATUS: Record<RemediationPRStatus, NonNullable<BadgeProps["tone"]>> =
  {
    creating: "info",
    open: "success",
    failed: "critical",
    superseded: "none",
  };

const VARIANT_BY_STATUS: Record<
  RemediationPRStatus,
  NonNullable<BadgeProps["variant"]>
> = {
  creating: "outline",
  open: "outline",
  failed: "outline",
  superseded: "muted",
};

export function RemediationPrStatusBadge({ status }: Props) {
  const { t } = useTranslation("remediation");
  return (
    <Badge
      variant={VARIANT_BY_STATUS[status]}
      tone={TONE_BY_STATUS[status]}
      data-testid="remediation-pr-status"
      data-status={status}
    >
      {t(`pr.status.${status}`)}
    </Badge>
  );
}
