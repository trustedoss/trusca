import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MultiSelect } from "@/components/ui/multi-select";
import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";
import {
  KNOWN_OBLIGATION_KINDS,
  type NoticeFormat,
  type ObligationSortKey,
  type SortOrder,
} from "@/features/projects/api/obligationsApi";
import { cn } from "@/lib/utils";

/** Formats exposed in the NOTICE download UI (markdown stays API-only). */
export const NOTICE_DOWNLOAD_FORMATS: NoticeFormat[] = ["text", "html"];

/**
 * ObligationsToolbar — Phase 3 PR #13.
 *
 * Inline filter row above the obligations list. Mirrors `LicensesToolbar`
 * (PR #12). The kind filter advertises the canonical KNOWN_OBLIGATION_KINDS
 * list; the catalog is open so unknown kinds may exist server-side, but
 * exposing them in the dropdown without a discovery endpoint would be a
 * usability dead-end.
 *
 * The toolbar also surfaces the NOTICE download button — a NOTICE is the
 * primary deliverable of this tab, so the action lives next to the filters
 * rather than buried in a project menu.
 */

export const SORT_OPTIONS: ObligationSortKey[] = [
  "category",
  "license_name",
  "kind",
  "affected_count",
];

export const CATEGORY_OPTIONS: LicenseCategoryName[] = [
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
];

export interface ObligationsToolbarProps {
  search: string;
  onSearchChange: (value: string) => void;
  kinds: string[];
  onKindsChange: (value: string[]) => void;
  categories: LicenseCategoryName[];
  onCategoriesChange: (value: LicenseCategoryName[]) => void;
  sort: ObligationSortKey;
  onSortChange: (value: ObligationSortKey) => void;
  order: SortOrder;
  onOrderChange: (value: SortOrder) => void;
  onDownloadNotice: (format: NoticeFormat) => void;
  isNoticeDownloading: boolean;
  noticeError: Error | null;
  className?: string;
}

export function ObligationsToolbar({
  search,
  onSearchChange,
  kinds,
  onKindsChange,
  categories,
  onCategoriesChange,
  sort,
  onSortChange,
  order,
  onOrderChange,
  onDownloadNotice,
  isNoticeDownloading,
  noticeError,
  className,
}: ObligationsToolbarProps) {
  const { t } = useTranslation("project_detail");
  const [noticeFormat, setNoticeFormat] = useState<NoticeFormat>("text");
  return (
    <div
      className={cn(
        "flex flex-col gap-3 border-b bg-background px-4 py-3 lg:flex-row lg:items-end lg:gap-4",
        className,
      )}
      data-testid="obligations-toolbar"
    >
      <div className="flex-1">
        <label
          htmlFor="obligations-search"
          className="block text-xs font-medium text-muted-foreground"
        >
          {t("obligations.toolbar.search_label")}
        </label>
        <Input
          id="obligations-search"
          type="search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder={t("obligations.toolbar.search_placeholder")}
          data-testid="obligations-search"
          className="mt-1 h-9"
        />
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="obligations-kind-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("obligations.toolbar.filter_kind")}
        </label>
        <MultiSelect
          id="obligations-kind-filter"
          testId="obligations-kind-filter"
          className="w-40"
          label={t("obligations.toolbar.filter_kind")}
          options={KNOWN_OBLIGATION_KINDS.map((opt) => ({
            value: opt,
            label: t(`obligations.kind.${opt}`, { defaultValue: opt }),
          }))}
          selected={kinds}
          onChange={onKindsChange}
        />
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="obligations-category-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("obligations.toolbar.filter_category")}
        </label>
        <MultiSelect
          id="obligations-category-filter"
          testId="obligations-category-filter"
          className="w-40"
          label={t("obligations.toolbar.filter_category")}
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
          htmlFor="obligations-sort"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("obligations.toolbar.sort_label")}
        </label>
        <select
          id="obligations-sort"
          value={sort}
          onChange={(event) =>
            onSortChange(event.target.value as ObligationSortKey)
          }
          className="mt-1 h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="obligations-sort"
        >
          {SORT_OPTIONS.map((key) => (
            <option key={key} value={key}>
              {t(`obligations.toolbar.sort.${key}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="obligations-order"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("obligations.toolbar.order_label")}
        </label>
        <select
          id="obligations-order"
          value={order}
          onChange={(event) => onOrderChange(event.target.value as SortOrder)}
          className="mt-1 h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="obligations-order"
        >
          <option value="asc">{t("obligations.toolbar.order_asc")}</option>
          <option value="desc">{t("obligations.toolbar.order_desc")}</option>
        </select>
      </div>

      <div className="flex flex-col">
        <span className="text-xs font-medium text-muted-foreground">
          {t("obligations.toolbar.notice_label")}
        </span>
        <div className="mt-1 flex gap-1">
          <select
            value={noticeFormat}
            onChange={(event) =>
              setNoticeFormat(event.target.value as NoticeFormat)
            }
            disabled={isNoticeDownloading}
            aria-label={t("obligations.toolbar.notice_format_label")}
            data-testid="obligations-notice-format"
            className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            {NOTICE_DOWNLOAD_FORMATS.map((fmt) => (
              <option key={fmt} value={fmt}>
                {t(`obligations.toolbar.notice_format_${fmt}`)}
              </option>
            ))}
          </select>
          <Button
            type="button"
            variant="default"
            size="sm"
            className="h-9"
            onClick={() => onDownloadNotice(noticeFormat)}
            disabled={isNoticeDownloading}
            data-testid="obligations-download-notice"
          >
            {isNoticeDownloading
              ? t("obligations.toolbar.notice_downloading")
              : t("obligations.toolbar.notice_download")}
          </Button>
        </div>
        {noticeError ? (
          <span
            className="mt-1 text-xs text-destructive"
            data-testid="obligations-download-notice-error"
          >
            {noticeError.message}
          </span>
        ) : null}
      </div>
    </div>
  );
}
