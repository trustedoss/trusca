import { FileSearch, Package, Stamp } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { LicenseFindingKind } from "@/features/projects/api/licensesApi";
import { cn } from "@/lib/utils";

/**
 * LicenseKindBadge — provenance of a license finding (PR-A2 / PR-A3).
 *
 * The pipeline emits two distinct families of license evidence:
 *
 *   - `declared`  — cdxgen read the license from a dependency's published
 *     metadata (package.json, POM, …). This is what a third-party package
 *     *says* it is.
 *   - `detected`  — scancode scanned the project's own first-party source
 *     and found a license header / file embedded in the code. This is the
 *     license actually shipped in your tree, independent of any manifest.
 *   - `concluded` — a human/ruleset override (reserved; rare in v2).
 *
 * Detected findings are the higher-signal, first-party evidence so they get
 * a distinct icon (`FileSearch` = "found in source") and a coloured tone,
 * while declared/dependency findings stay neutral. Color is never the only
 * signal — every badge pairs the tone with an icon AND a localized word.
 */
type Tone = "info" | "low" | "none";

const TONE_BY_KIND: Record<LicenseFindingKind, Tone> = {
  detected: "low",
  declared: "none",
  concluded: "info",
};

const ICON_BY_KIND: Record<
  LicenseFindingKind,
  typeof FileSearch
> = {
  detected: FileSearch,
  declared: Package,
  concluded: Stamp,
};

export interface LicenseKindBadgeProps {
  kind: LicenseFindingKind;
  className?: string;
}

export function LicenseKindBadge({ kind, className }: LicenseKindBadgeProps) {
  const { t } = useTranslation("project_detail");
  const Icon = ICON_BY_KIND[kind];
  return (
    <Badge
      tone={TONE_BY_KIND[kind]}
      variant={kind === "declared" ? "outline" : undefined}
      data-testid={`license-kind-badge-${kind}`}
      data-license-kind={kind}
      className={cn("gap-1", className)}
    >
      <Icon className="h-3 w-3" aria-hidden />
      <span>{t(`licenses.kind.${kind}`)}</span>
    </Badge>
  );
}
