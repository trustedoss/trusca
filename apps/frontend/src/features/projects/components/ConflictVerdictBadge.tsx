// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { Ban, Check, CircleHelp, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { ConflictVerdictName } from "@/features/projects/api/licensesApi";
import { cn } from "@/lib/utils";

/**
 * ConflictVerdictBadge — how a license sits against the project's declared
 * outbound license (gap #27).
 *
 * The verdict is computed on the server and arrives on the row, reason
 * included; this component only chooses how to present it. There is no
 * classification logic here on purpose — a second implementation in the UI is
 * what makes a badge and a report disagree.
 *
 * Tones read worst-first the way the rest of the app does: `incompatible` takes
 * the critical tone, `conditional` the medium (amber) tone the app already uses
 * for "needs legal review", `unknown` the neutral info tone, and `compatible`
 * success.
 *
 * Accessibility: colour is never the only signal. Each verdict pairs its tone
 * with a distinct icon AND its localized word, and the `title` carries the
 * reason sentence so the justification is reachable without opening the drawer.
 *
 * Advisory: nothing here is a legal determination, and the tab says so next to
 * the column.
 */
export interface ConflictVerdictBadgeProps {
  verdict: ConflictVerdictName;
  /** The reason sentence from the rule data — shown as the tooltip. */
  why?: string;
  className?: string;
}

const TONE: Record<
  ConflictVerdictName,
  "critical" | "medium" | "info" | "success"
> = {
  incompatible: "critical",
  conditional: "medium",
  unknown: "info",
  compatible: "success",
};

const ICON: Record<ConflictVerdictName, typeof Check> = {
  incompatible: Ban,
  conditional: TriangleAlert,
  unknown: CircleHelp,
  compatible: Check,
};

export function ConflictVerdictBadge({
  verdict,
  why,
  className,
}: ConflictVerdictBadgeProps) {
  const { t } = useTranslation("project_detail");
  const Icon = ICON[verdict];
  return (
    <Badge
      tone={TONE[verdict]}
      data-testid="license-conflict-badge"
      data-verdict={verdict}
      title={why ?? t(`licenses.conflict.verdict.${verdict}`)}
      className={cn("gap-1", className)}
    >
      <Icon className="h-3 w-3" aria-hidden />
      <span>{t(`licenses.conflict.verdict.${verdict}`)}</span>
    </Badge>
  );
}
