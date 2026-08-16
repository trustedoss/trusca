// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useTranslation } from "react-i18next";

import { ExportCsvButton } from "@/components/ExportCsvButton";
import { MoreFiltersMenu } from "@/components/filters/MoreFiltersMenu";
import { Input } from "@/components/ui/input";
import { MultiSelect } from "@/components/ui/multi-select";
import { useState } from "react";

import type {
  InventorySortKey,
  SortOrder,
} from "@/features/inventory/api/inventoryApi";
import type {
  ComponentSeverity,
  LicenseCategoryName,
} from "@/features/projects/api/projectDetailApi";

/**
 * Inventory filter bar.
 *
 * Search, package type and sort are always visible because they are how the
 * page is normally driven. The risk axes (severity, licence) and the lifecycle
 * flags are opt-in through "Add filter" — the same progressive-disclosure
 * pattern the Components tab uses, so the default bar stays readable on a
 * portfolio-wide view that already carries a lot of numbers.
 */

const SEVERITY_OPTIONS: ComponentSeverity[] = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
  "none",
];
const LICENSE_OPTIONS: LicenseCategoryName[] = [
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
];
/** The ecosystems cdxgen emits most often; free-form values still filter. */
const PACKAGE_TYPE_OPTIONS = [
  "npm",
  "pypi",
  "maven",
  "golang",
  "cargo",
  "nuget",
  "gem",
  "composer",
];

const OPTIONAL_FILTER_IDS = ["severity", "license_category", "lifecycle"] as const;
type OptionalFilterId = (typeof OPTIONAL_FILTER_IDS)[number];

export interface InventoryToolbarProps {
  search: string;
  onSearchChange: (next: string) => void;
  packageType: string[];
  onPackageTypeChange: (next: string[]) => void;
  severity: ComponentSeverity[];
  onSeverityChange: (next: ComponentSeverity[]) => void;
  licenseCategory: LicenseCategoryName[];
  onLicenseCategoryChange: (next: LicenseCategoryName[]) => void;
  eol: boolean | undefined;
  onEolChange: (next: boolean | undefined) => void;
  outdated: boolean | undefined;
  onOutdatedChange: (next: boolean | undefined) => void;
  sort: InventorySortKey;
  onSortChange: (next: InventorySortKey) => void;
  order: SortOrder;
  onOrderChange: (next: SortOrder) => void;
  /**
   * B5: download the currently filtered inventory as CSV. The page owns
   * this because the page owns the filter state the export has to match.
   */
  onExportCsv: () => Promise<void>;
}

export function InventoryToolbar({
  search,
  onSearchChange,
  packageType,
  onPackageTypeChange,
  severity,
  onSeverityChange,
  licenseCategory,
  onLicenseCategoryChange,
  eol,
  onEolChange,
  outdated,
  onOutdatedChange,
  sort,
  onSortChange,
  order,
  onOrderChange,
  onExportCsv,
}: InventoryToolbarProps) {
  const { t } = useTranslation("inventory");

  // A filter stays revealed once opened, even after it is cleared, so clearing
  // a selection does not make the control vanish under the cursor.
  const [revealed, setRevealed] = useState<Set<OptionalFilterId>>(() => {
    const initial = new Set<OptionalFilterId>();
    if (severity.length) initial.add("severity");
    if (licenseCategory.length) initial.add("license_category");
    if (eol !== undefined || outdated !== undefined) initial.add("lifecycle");
    return initial;
  });

  const availableFilters = OPTIONAL_FILTER_IDS.filter(
    (id) => !revealed.has(id),
  ).map((id) => ({ id, label: t(`toolbar.filter.${id}`) }));

  return (
    <div
      className="flex flex-wrap items-end gap-3 border-b px-6 py-3"
      data-testid="inventory-toolbar"
    >
      <div className="flex min-w-[240px] flex-col">
        <label htmlFor="inventory-search" className="text-xs text-muted-foreground">
          {t("toolbar.search_label")}
        </label>
        <Input
          id="inventory-search"
          type="search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder={t("toolbar.search_placeholder")}
          data-testid="inventory-search"
          className="mt-1 h-9"
        />
      </div>

      <MultiSelect
        label={t("toolbar.package_type")}
        options={PACKAGE_TYPE_OPTIONS.map((value) => ({
          value,
          label: value,
        }))}
        selected={packageType}
        onChange={onPackageTypeChange}
        testId="inventory-package-type"
      />

      {revealed.has("severity") ? (
        <MultiSelect
          label={t("toolbar.filter.severity")}
          options={SEVERITY_OPTIONS.map((value) => ({
            value,
            label: t(`severity.${value}`),
          }))}
          selected={severity}
          onChange={(next) => onSeverityChange(next as ComponentSeverity[])}
          testId="inventory-severity"
        />
      ) : null}

      {revealed.has("license_category") ? (
        <MultiSelect
          label={t("toolbar.filter.license_category")}
          options={LICENSE_OPTIONS.map((value) => ({
            value,
            label: t(`license.${value}`),
          }))}
          selected={licenseCategory}
          onChange={(next) =>
            onLicenseCategoryChange(next as LicenseCategoryName[])
          }
          testId="inventory-license-category"
        />
      ) : null}

      {revealed.has("lifecycle") ? (
        <div className="flex flex-col" data-testid="inventory-lifecycle">
          <span className="text-xs text-muted-foreground">
            {t("toolbar.filter.lifecycle")}
          </span>
          <div className="mt-1 flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={eol === true}
                onChange={(event) =>
                  onEolChange(event.target.checked ? true : undefined)
                }
                data-testid="inventory-eol-toggle"
              />
              {t("toolbar.eol_only")}
            </label>
            <label className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={outdated === true}
                onChange={(event) =>
                  onOutdatedChange(event.target.checked ? true : undefined)
                }
                data-testid="inventory-outdated-toggle"
              />
              {t("toolbar.outdated_only")}
            </label>
          </div>
        </div>
      ) : null}

      <MoreFiltersMenu
        availableFilters={availableFilters}
        activeFilterIds={revealed}
        onSelect={(id) =>
          setRevealed((prev) => new Set(prev).add(id as OptionalFilterId))
        }
        testId="inventory-more-filters"
      />

      <div className="ml-auto flex items-end gap-2">
        <div className="flex flex-col">
          <label htmlFor="inventory-sort" className="text-xs text-muted-foreground">
            {t("toolbar.sort_label")}
          </label>
          <select
            id="inventory-sort"
            value={sort}
            onChange={(event) =>
              onSortChange(event.target.value as InventorySortKey)
            }
            data-testid="inventory-sort"
            className="mt-1 h-9 rounded-md border bg-background px-2 text-sm"
          >
            <option value="project_count">{t("toolbar.sort.project_count")}</option>
            <option value="name">{t("toolbar.sort.name")}</option>
            <option value="severity">{t("toolbar.sort.severity")}</option>
            <option value="license">{t("toolbar.sort.license")}</option>
          </select>
        </div>
        <select
          aria-label={t("toolbar.order_label")}
          value={order}
          onChange={(event) => onOrderChange(event.target.value as SortOrder)}
          data-testid="inventory-order"
          className="h-9 rounded-md border bg-background px-2 text-sm"
        >
          <option value="desc">{t("toolbar.order.desc")}</option>
          <option value="asc">{t("toolbar.order.asc")}</option>
        </select>
      </div>

      {/* B5: the rows as they are filtered right now. */}
      <ExportCsvButton
        onExport={() => onExportCsv()}
        namespace="inventory"
        tooLargeExtension="inventory_export_too_large"
        tooLargeMessageKey="export.too_large.inventory"
        data-testid="inventory-export-csv"
      />
    </div>
  );
}
