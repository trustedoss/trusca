import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";
import { MultiSelect } from "@/components/ui/multi-select";
import type {
  LicenseCategoryName,
  LicenseFindingKind,
  LicenseSortKey,
  ReviewFlag,
  SortOrder,
} from "@/features/projects/api/licensesApi";
import { REVIEW_FLAG_VALUES } from "@/features/projects/api/licensesApi";
import { cn } from "@/lib/utils";

/**
 * LicensesToolbar — Phase 3 PR #12.
 *
 * Inline filter row above the virtualized licenses list. Mirrors the shape
 * of `VulnerabilitiesToolbar` (CLAUDE.md "디자인 시스템": filters appear
 * inline at the top of lists, no modal filter dialogs). Category and kind
 * use the reusable `MultiSelect` (app-i18n checkbox dropdown); the search
 * input is debounced upstream in the tab.
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

/** Single-select review-flag facet options (Phase D). Mirrors the backend. */
export const REVIEW_FLAG_OPTIONS: ReviewFlag[] = [...REVIEW_FLAG_VALUES];

export interface LicensesToolbarProps {
  search: string;
  onSearchChange: (value: string) => void;
  categories: LicenseCategoryName[];
  onCategoriesChange: (value: LicenseCategoryName[]) => void;
  kinds: LicenseFindingKind[];
  onKindsChange: (value: LicenseFindingKind[]) => void;
  reviewFlag: ReviewFlag | undefined;
  onReviewFlagChange: (value: ReviewFlag | undefined) => void;
  sort: LicenseSortKey;
  onSortChange: (value: LicenseSortKey) => void;
  order: SortOrder;
  onOrderChange: (value: SortOrder) => void;
  className?: string;
}

export function LicensesToolbar({
  search,
  onSearchChange,
  categories,
  onCategoriesChange,
  kinds,
  onKindsChange,
  reviewFlag,
  onReviewFlagChange,
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
        <MultiSelect
          id="licenses-category-filter"
          testId="licenses-category-filter"
          className="w-40"
          label={t("licenses.toolbar.filter_category")}
          options={CATEGORY_OPTIONS.map((opt) => ({
            value: opt,
            label: t(`license_category.${opt}`),
          }))}
          selected={categories}
          onChange={(next) => onCategoriesChange(next as LicenseCategoryName[])}
        />
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="licenses-kind-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("licenses.toolbar.filter_kind")}
        </label>
        <MultiSelect
          id="licenses-kind-filter"
          testId="licenses-kind-filter"
          className="w-40"
          label={t("licenses.toolbar.filter_kind")}
          options={KIND_OPTIONS.map((opt) => ({
            value: opt,
            label: t(`licenses.kind.${opt}`),
          }))}
          selected={kinds}
          onChange={(next) => onKindsChange(next as LicenseFindingKind[])}
        />
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="licenses-review-flag"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("licenses.toolbar.filter_review_flag")}
        </label>
        <select
          id="licenses-review-flag"
          value={reviewFlag ?? ""}
          onChange={(event) =>
            onReviewFlagChange(
              event.target.value
                ? (event.target.value as ReviewFlag)
                : undefined,
            )
          }
          className="mt-1 h-9 w-40 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="licenses-review-flag"
        >
          <option value="">{t("licenses.toolbar.review_flag.all")}</option>
          {REVIEW_FLAG_OPTIONS.map((flag) => (
            <option key={flag} value={flag}>
              {t(`licenses.review.short.${flag}`)}
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
