// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/primer/badge";
import { cn } from "@/lib/utils";

/**
 * DependencyScopeBadge — W2 #31.
 *
 * commercial-SCA-style "Usage" facet. Mirrors the chosen (shallowest) path's raw
 * dependency scope:
 *
 *   - `required`   → load-bearing at runtime / build time. Emerald dot.
 *   - `optional`   → optional / dev-only edge. Amber dot (uses the medium
 *     risk token so it stays in-palette with the rest of the row).
 *   - `null`       → cdxgen did not encode a scope on the edge (very common
 *     for SBOMs that don't carry scope at all). Render an em-dash — DO NOT
 *     fall back to "Required", that would be a lie about the SBOM.
 *
 * Color is never the only signal — every variant carries a localized label.
 */

export type DependencyScope = "required" | "optional" | null;

export interface DependencyScopeBadgeProps {
  scope: DependencyScope;
  className?: string;
}

export function DependencyScopeBadge({
  scope,
  className,
}: DependencyScopeBadgeProps) {
  const { t } = useTranslation("project_detail");

  if (scope === "required") {
    return (
      <Badge
        tone="success"
        data-testid="dependency-scope-badge"
        data-dependency-scope="required"
        className={cn("gap-1.5", className)}
      >
        <span
          aria-hidden
          className="inline-block h-1.5 w-1.5 rounded-full bg-status-success"
        />
        <span>{t("components.badge.dependency_scope.required")}</span>
      </Badge>
    );
  }

  if (scope === "optional") {
    return (
      <Badge
        tone="warning"
        data-testid="dependency-scope-badge"
        data-dependency-scope="optional"
        className={cn("gap-1.5", className)}
      >
        <span
          aria-hidden
          className="inline-block h-1.5 w-1.5 rounded-full bg-status-warning-foreground"
        />
        <span>{t("components.badge.dependency_scope.optional")}</span>
      </Badge>
    );
  }

  // Plain muted text, not a badge (see DependencyTypeBadge): most scans
  // leave the scope empty, and a grey chip in every row read as a value.
  return (
    <span
      data-testid="dependency-scope-badge"
      data-dependency-scope="unknown"
      title={t("components.badge.dependency_scope.aria_unknown")}
      className={cn("text-xs text-muted-foreground", className)}
    >
      <span aria-hidden>{t("components.badge.dependency_scope.unknown")}</span>
      <span className="sr-only">
        {t("components.badge.dependency_scope.aria_unknown")}
      </span>
    </span>
  );
}
