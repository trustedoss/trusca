import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";
import type {
  LicenseCategoryName,
  LicenseFindingKind,
  LicenseSortKey,
  SortOrder,
} from "@/features/projects/api/licensesApi";
import { cn } from "@/lib/utils";

/**
 * LicensesToolbar — Phase 3 PR #12.
 *
 * Inline filter row above the virtualized licenses list. Mirrors the shape
 * of `VulnerabilitiesToolbar` (CLAUDE.md "디자인 시스템": filters appear
 * inline at the top of lists, no modal filter dialogs). Category and kind
 * are native `<select multiple>` to avoid a new dependency; the search input
 * is debounced upstream in the tab.
 *
 * Search matches license name, SPDX id, and (server-side) the underlying
 * license catalog row. The input placeholder advertises both axes so the
 * user doesn't think it only matches names.
 */

export const CATEGORY_OPTIONS: LicenseCategoryName[] = [
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
];

export const KIND_OPTIONS: LicenseFindingKind[] = [
  "declared",
  "concluded",
  "detected",
];

export const SORT_OPTIONS: LicenseSortKey[] = [
  "category",
  "name",
  "spdx_id",
  "affected_count",
];

export interface LicensesToolbarProps {
  search: string;
  onSearchChange: (value: string) => void;
  categories: LicenseCategoryName[];
  onCategoriesChange: (value: LicenseCategoryName[]) => void;
  kinds: LicenseFindingKind[];
  onKindsChange: (value: LicenseFindingKind[]) => void;
  sort: LicenseSortKey;
  onSortChange: (value: LicenseSortKey) => void;
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

export function LicensesToolbar({
  search,
  onSearchChange,
  categories,
  onCategoriesChange,
  kinds,
  onKindsChange,
  sort,
  onSortChange,
  order,
  onOrderChange,
  className,
}: LicensesToolbarProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className={cn(
        "flex flex-col gap-3 border-b bg-background px-4 py-3 lg:flex-row lg:items-end lg:gap-4",
        className,
      )}
      data-testid="licenses-toolbar"
    >
      <div className="flex-1">
        <label
          htmlFor="licenses-search"
          className="block text-xs font-medium text-muted-foreground"
        >
          {t("licenses.toolbar.search_label")}
        </label>
        <Input
          id="licenses-search"
          type="search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder={t("licenses.toolbar.search_placeholder")}
          data-testid="licenses-search"
          className="mt-1 h-9"
        />
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="licenses-category-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("licenses.toolbar.filter_category")}
        </label>
        <select
          id="licenses-category-filter"
          multiple
          size={1}
          value={categories}
          onChange={(event) =>
            onCategoriesChange(selectedValues<LicenseCategoryName>(event))
          }
          className="mt-1 h-9 w-40 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="licenses-category-filter"
        >
          {CATEGORY_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(`license_category.${opt}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="licenses-kind-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("licenses.toolbar.filter_kind")}
        </label>
        <select
          id="licenses-kind-filter"
          multiple
          size={1}
          value={kinds}
          onChange={(event) =>
            onKindsChange(selectedValues<LicenseFindingKind>(event))
          }
          className="mt-1 h-9 w-40 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="licenses-kind-filter"
        >
          {KIND_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(`licenses.kind.${opt}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="licenses-sort"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("licenses.toolbar.sort_label")}
        </label>
        <select
          id="licenses-sort"
          value={sort}
          onChange={(event) =>
            onSortChange(event.target.value as LicenseSortKey)
          }
          className="mt-1 h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="licenses-sort"
        >
          {SORT_OPTIONS.map((key) => (
            <option key={key} value={key}>
              {t(`licenses.toolbar.sort.${key}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="licenses-order"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("licenses.toolbar.order_label")}
        </label>
        <select
          id="licenses-order"
          value={order}
          onChange={(event) => onOrderChange(event.target.value as SortOrder)}
          className="mt-1 h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="licenses-order"
        >
          <option value="asc">{t("licenses.toolbar.order_asc")}</option>
          <option value="desc">{t("licenses.toolbar.order_desc")}</option>
        </select>
      </div>
    </div>
  );
}
