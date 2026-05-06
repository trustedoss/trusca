import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";
import type {
  ComponentSeverity,
  ComponentSortKey,
  LicenseCategoryName,
  SortOrder,
} from "@/features/projects/api/projectDetailApi";
import { cn } from "@/lib/utils";

/**
 * ComponentsToolbar — Phase 3 PR #10.
 *
 * Inline filter row above the virtualized component list. CLAUDE.md
 * "디자인 시스템" — filters appear inline at the top of lists, no modal
 * filter dialogs. Multi-select severity/license filters are rendered as
 * native `<select multiple>` for now (zero new dependency); the sort key
 * and direction are paired so deep-links remain stable.
 */

export const SEVERITY_OPTIONS: ComponentSeverity[] = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
  "none",
];

export const LICENSE_OPTIONS: LicenseCategoryName[] = [
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
];

export const SORT_OPTIONS: ComponentSortKey[] = [
  "name",
  "severity",
  "license",
];

export interface ComponentsToolbarProps {
  search: string;
  onSearchChange: (value: string) => void;
  severity: ComponentSeverity[];
  onSeverityChange: (value: ComponentSeverity[]) => void;
  licenseCategory: LicenseCategoryName[];
  onLicenseCategoryChange: (value: LicenseCategoryName[]) => void;
  sort: ComponentSortKey;
  onSortChange: (value: ComponentSortKey) => void;
  order: SortOrder;
  onOrderChange: (value: SortOrder) => void;
  className?: string;
}

function selectedValues<T extends string>(
  event: React.ChangeEvent<HTMLSelectElement>,
): T[] {
  return Array.from(event.target.selectedOptions).map(
    (opt) => opt.value as T,
  );
}

export function ComponentsToolbar({
  search,
  onSearchChange,
  severity,
  onSeverityChange,
  licenseCategory,
  onLicenseCategoryChange,
  sort,
  onSortChange,
  order,
  onOrderChange,
  className,
}: ComponentsToolbarProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className={cn(
        "flex flex-col gap-3 border-b bg-background px-4 py-3 lg:flex-row lg:items-end lg:gap-4",
        className,
      )}
      data-testid="components-toolbar"
    >
      <div className="flex-1">
        <label
          htmlFor="components-search"
          className="block text-xs font-medium text-muted-foreground"
        >
          {t("components.toolbar.search_label")}
        </label>
        <Input
          id="components-search"
          type="search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder={t("components.toolbar.search_placeholder")}
          data-testid="components-search"
          className="mt-1 h-9"
        />
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="components-severity-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("components.toolbar.severity_label")}
        </label>
        <select
          id="components-severity-filter"
          multiple
          size={1}
          value={severity}
          onChange={(event) =>
            onSeverityChange(selectedValues<ComponentSeverity>(event))
          }
          className="mt-1 h-9 w-40 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="components-severity-filter"
        >
          {SEVERITY_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(`severity.${opt}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="components-license-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("components.toolbar.license_label")}
        </label>
        <select
          id="components-license-filter"
          multiple
          size={1}
          value={licenseCategory}
          onChange={(event) =>
            onLicenseCategoryChange(selectedValues<LicenseCategoryName>(event))
          }
          className="mt-1 h-9 w-40 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="components-license-filter"
        >
          {LICENSE_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(`license_category.${opt}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="components-sort"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("components.toolbar.sort_label")}
        </label>
        <select
          id="components-sort"
          value={sort}
          onChange={(event) =>
            onSortChange(event.target.value as ComponentSortKey)
          }
          className="mt-1 h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="components-sort"
        >
          {SORT_OPTIONS.map((key) => (
            <option key={key} value={key}>
              {t(`components.toolbar.sort_by_${key}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="components-order"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("components.toolbar.order_label")}
        </label>
        <select
          id="components-order"
          value={order}
          onChange={(event) => onOrderChange(event.target.value as SortOrder)}
          className="mt-1 h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="components-order"
        >
          <option value="asc">{t("components.toolbar.order_asc")}</option>
          <option value="desc">{t("components.toolbar.order_desc")}</option>
        </select>
      </div>
    </div>
  );
}
