// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { Check, CircleHelp, OctagonAlert, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { AiVerdict } from "@/lib/projectsApi";
import { cn } from "@/lib/utils";

/**
 * AiVerdictBadge: how a model or dataset reads for the project's intended use
 * (gap #28).
 *
 * The verdict is computed on the server against the project's usage scenario
 * and arrives on the row. Nothing is classified here: a second implementation
 * in the UI is what makes a badge and a report disagree.
 *
 * Tones deliberately do NOT reuse the risk palette. This is not a severity: a
 * `caution` model is not a critical vulnerability, and dressing it in the same
 * red would put a licensing question and an exploitable CVE on one scale. The
 * four tones are the same set the outbound-conflict badge uses, mapped to this
 * axis's ranking: caution (a blocker somebody must resolve), review (nothing
 * classified it, which outranks a known obligation), conditional (obligations
 * that bind this use), ok.
 *
 * Accessibility: colour is never the only signal. Each verdict pairs its tone
 * with a distinct icon AND its localized word, and the `title` carries the
 * registry's summary so the justification is reachable without expanding.
 *
 * Advisory: nothing here is a legal determination, and the section says so
 * beside it.
 */
export interface AiVerdictBadgeProps {
  verdict: AiVerdict;
  /** Registry summary for the license that drove it, shown as the tooltip. */
  summary?: string;
  className?: string;
}

const TONE: Record<AiVerdict, "critical" | "medium" | "info" | "success"> = {
  caution: "critical",
  review: "info",
  conditional: "medium",
  ok: "success",
};

const ICON: Record<AiVerdict, typeof Check> = {
  caution: OctagonAlert,
  review: CircleHelp,
  conditional: TriangleAlert,
  ok: Check,
};

export function AiVerdictBadge({
  verdict,
  summary,
  className,
}: AiVerdictBadgeProps) {
  const { t } = useTranslation("scans");
  const Icon = ICON[verdict];
  const label = t(`conformance.ai.verdict.${verdict}`);
  return (
    <Badge
      tone={TONE[verdict]}
      data-testid="ai-verdict-badge"
      data-verdict={verdict}
      title={summary ?? label}
      className={cn("gap-1", className)}
    >
      <Icon className="h-3 w-3" aria-hidden />
      <span>{label}</span>
    </Badge>
  );
}
