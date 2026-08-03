// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { InventoryComponentRow } from "@/features/inventory/api/inventoryApi";
import { CurrencyBadge } from "@/features/projects/components/CurrencyBadge";
import { EolBadge } from "@/features/projects/components/EolBadge";
import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";
import { cn } from "@/lib/utils";

export interface InventoryRowProps {
  row: InventoryComponentRow;
  rowIndex: number;
  onOpen: () => void;
}

/**
 * One package in organization-wide use.
 *
 * The whole row is the affordance — clicking it opens the usage drawer, which
 * is the only reason to be on this page — so it is a button, not a div with a
 * handler. `data-*` mirrors carry the values the E2E harness asserts on so no
 * test has to parse formatted copy.
 */
export function InventoryRow({ row, rowIndex, onOpen }: InventoryRowProps) {
  const { t } = useTranslation("inventory");

  const versionLabel =
    row.version_count > row.versions.length
      ? t("row.versions_truncated", {
          shown: row.versions.join(", "),
          more: row.version_count - row.versions.length,
        })
      : row.versions.join(", ");

  return (
    <button
      type="button"
      role="row"
      aria-rowindex={rowIndex + 2}
      onClick={onOpen}
      data-testid="inventory-row"
      data-component-id={row.component_id}
      data-project-count={row.project_count}
      data-version-count={row.version_count}
      data-severity={row.severity_max}
      className={cn(
        "flex w-full items-center gap-3 border-b py-2 text-left text-sm",
        "transition-colors duration-fast ease-out-soft hover:bg-accent",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      )}
      style={{ height: "var(--table-row)" }}
    >
      <span role="cell" className="flex min-w-0 flex-1 flex-col">
        <span className="truncate font-medium" title={row.name}>
          {row.name}
        </span>
        <span
          className="truncate font-mono text-xs text-muted-foreground"
          title={row.purl}
        >
          {row.purl}
        </span>
      </span>

      <span role="cell" className="w-24">
        <Badge>{row.package_type}</Badge>
      </span>

      {/* The number this page exists to show. The column header already
          says what it counts, so the cell is just the figure — same as the
          CVEs column beside it. */}
      <span
        role="cell"
        className="w-28 font-medium tabular-nums"
        data-testid="inventory-row-projects"
      >
        {row.project_count}
      </span>

      <span
        role="cell"
        className="w-40 truncate font-mono text-xs text-muted-foreground"
        title={row.versions.join(", ")}
      >
        {versionLabel}
      </span>

      <span role="cell" className="w-24">
        <SeverityBadge severity={row.severity_max} />
      </span>

      <span role="cell" className="w-20 tabular-nums">
        {row.vulnerability_count > 0 ? row.vulnerability_count : "—"}
      </span>

      <span role="cell" className="flex w-32 items-center gap-1">
        <LicenseCategoryBadge category={row.license_category_max} />
        {/* Lifecycle flags are additive signals, not a column of their own —
            they only ever appear when true, so an absent badge is the "fine"
            state rather than a blank cell to interpret. */}
        {row.eol ? <EolBadge eolState="eol" /> : null}
        {row.outdated ? <CurrencyBadge currencyState="outdated" /> : null}
      </span>
    </button>
  );
}
