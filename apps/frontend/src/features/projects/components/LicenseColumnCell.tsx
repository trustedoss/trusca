import { useTranslation } from "react-i18next";

import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { cn } from "@/lib/utils";

/**
 * LicenseColumnCell — W4-B-prep shared primitive.
 *
 * Splits the previous "single LICENSE = Allowed/Forbidden/Conditional"
 * column into two coordinated facets the user can actually act on:
 *
 *   1. The actual SPDX identifier (e.g. `MIT`, `Apache-2.0`) — needed to
 *      tell two Allowed components apart, drives Compliance/NOTICE drill-in.
 *   2. The policy category badge (Allowed / Forbidden / Conditional) —
 *      the risk lens used by all current toolbars/filters.
 *
 * W4-B will swap this into ComponentsTab + VulnerabilitiesTab. Until then
 * the primitive ships on its own so the diff stays small.
 *
 * Layout: SPDX above (mono, small), badge below. Stacked rather than inline
 * to survive long SPDX expressions (`(MIT OR Apache-2.0)`) without forcing
 * the row to wrap.
 */

export interface LicenseColumnCellProps {
  /**
   * SPDX identifier as returned by the backend. `null` when the component
   * has no detected license — we show a localized dash rather than blank
   * so empty cells are obvious.
   */
  license: string | null;
  /** Policy category — never null on the backend (defaults to `unknown`). */
  category: LicenseCategoryName;
  className?: string;
}

export function LicenseColumnCell({
  license,
  category,
  className,
}: LicenseColumnCellProps) {
  const { t } = useTranslation("project_detail");
  const spdxText = license ?? t("components.license.unknown_dash");

  return (
    <div
      data-testid="license-column-cell"
      data-license-spdx={license ?? ""}
      data-license-category={category}
      className={cn("flex flex-col gap-1", className)}
    >
      <span
        className={cn(
          "truncate font-mono text-xs",
          license ? "text-foreground" : "text-muted-foreground",
        )}
        title={spdxText}
      >
        {spdxText}
      </span>
      <LicenseCategoryBadge category={category} />
    </div>
  );
}
