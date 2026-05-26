import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";
import { MultiSelect } from "@/components/ui/multi-select";
import type {
  ComponentSeverity,
  ComponentSortKey,
  DependencyScopeFilter,
  LicenseCategoryName,
  SortOrder,
} from "@/features/projects/api/projectDetailApi";
import { cn } from "@/lib/utils";

/**
 * ComponentsToolbar — Phase 3 PR #10.
 *
 * Inline filter row above the virtualized component list. CLAUDE.md
 * "디자인 시스템" — filters appear inline at the top of lists, no modal
 * filter dialogs. The severity/license filters use the reusable
 * `MultiSelect` (app-i18n checkbox dropdown) so the collapsed trigger label
 * is driven by the app language, not the OS locale; the sort key and
 * direction are paired so deep-links remain stable.
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

/**
 * W2 #31 — BD-style "Usage" facet options. Mirrors the backend's accepted
 * values for ``?dependency_scope=``; ``unspecified`` selects the NULL-scope
 * bucket (cdxgen often produces no scope on edges).
 */
export const USAGE_OPTIONS: DependencyScopeFilter[] = [
  "required",
  "optional",
  "unspecified",
];

/**
 * W2 #31 — Dependency-type segmented control values. ``null`` means "All"
 * (no `?direct=` on the wire), ``true`` keeps only direct, ``false`` only
 * transitive (and the depth-null bucket).
 */
const DEPENDENCY_TYPE_OPTIONS: { value: boolean | null; key: string }[] = [
  { value: null, key: "all" },
  { value: true, key: "direct" },
  { value: false, key: "transitive" },
];

export interface ComponentsToolbarProps {
  search: string;
  onSearchChange: (value: string) => void;
  severity: ComponentSeverity[];
  onSeverityChange: (value: ComponentSeverity[]) => void;
  licenseCategory: LicenseCategoryName[];
  onLicenseCategoryChange: (value: LicenseCategoryName[]) => void;
  /** Dependency-type 3-state (null/true/false). */
  direct: boolean | null;
  onDirectChange: (value: boolean | null) => void;
  /** BD-style "Usage" multi-select. */
  dependencyScope: DependencyScopeFilter[];
  onDependencyScopeChange: (value: DependencyScopeFilter[]) => void;
  sort: ComponentSortKey;
  onSortChange: (value: ComponentSortKey) => void;
  order: SortOrder;
  onOrderChange: (value: SortOrder) => void;
  className?: string;
}

export function ComponentsToolbar({
  search,
  onSearchChange,
  severity,
  onSeverityChange,
  licenseCategory,
  onLicenseCategoryChange,
  direct,
  onDirectChange,
  dependencyScope,
  onDependencyScopeChange,
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
        <MultiSelect
          id="components-severity-filter"
          testId="components-severity-filter"
          className="w-40"
          label={t("components.toolbar.severity_label")}
          options={SEVERITY_OPTIONS.map((opt) => ({
            value: opt,
            label: t(`severity.${opt}`),
          }))}
          selected={severity}
          onChange={(next) => onSeverityChange(next as ComponentSeverity[])}
        />
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="components-license-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("components.toolbar.license_label")}
        </label>
        <MultiSelect
          id="components-license-filter"
          testId="components-license-filter"
          className="w-40"
          label={t("components.toolbar.license_label")}
          options={LICENSE_OPTIONS.map((opt) => ({
            value: opt,
            label: t(`license_category.${opt}`),
          }))}
          selected={licenseCategory}
          onChange={(next) =>
            onLicenseCategoryChange(next as LicenseCategoryName[])
          }
        />
      </div>

      <div
        className="flex flex-col"
        data-testid="components-dependency-type-filter"
      >
        <span className="text-xs font-medium text-muted-foreground">
          {t("components.toolbar.dependency_type_label")}
        </span>
        <div
          role="group"
          aria-label={t("components.toolbar.dependency_type_label")}
          className="mt-1 inline-flex h-9 items-stretch overflow-hidden rounded-md border border-input bg-background"
        >
          {DEPENDENCY_TYPE_OPTIONS.map((opt, idx) => {
            const isActive = direct === opt.value;
            return (
              <button
                key={opt.key}
                type="button"
                aria-pressed={isActive}
                data-testid={`components-dependency-type-${opt.key}`}
                data-active={isActive ? "true" : "false"}
                onClick={() => onDirectChange(opt.value)}
                className={cn(
                  "px-3 text-xs font-medium",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
                  idx > 0 && "border-l border-input",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                {t(`components.toolbar.dependency_type.${opt.key}`)}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="components-usage-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("components.toolbar.usage_label")}
        </label>
        <MultiSelect
          id="components-usage-filter"
          testId="components-usage-filter"
          className="w-40"
          label={t("components.toolbar.usage_label")}
          options={USAGE_OPTIONS.map((opt) => ({
            value: opt,
            label: t(`components.toolbar.usage.${opt}`),
          }))}
          selected={dependencyScope}
          onChange={(next) =>
            onDependencyScopeChange(next as DependencyScopeFilter[])
          }
        />
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
