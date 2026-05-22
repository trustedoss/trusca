import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type {
  SortOrder,
  VulnFindingStatus,
  VulnSeverity,
  VulnerabilitySortKey,
} from "@/features/projects/api/vulnerabilitiesApi";
import { ALL_VULNERABILITY_STATUSES } from "@/features/projects/lib/vulnerabilityTransitions";
import { cn } from "@/lib/utils";

/**
 * VulnerabilitiesToolbar — Phase 3 PR #11.
 *
 * Inline filter row above the virtualized vulnerabilities list. Mirrors the
 * shape of `ComponentsToolbar` (CLAUDE.md "디자인 시스템": filters appear
 * inline at the top of lists, no modal filter dialogs). Severity and status
 * are native `<select multiple>` to avoid a new dependency; the search input
 * is debounced upstream in the tab.
 */

export const SEVERITY_OPTIONS: VulnSeverity[] = [
  "critical",
  "high",
  "medium",
  "low",
  "info",
  "unknown",
];

export const STATUS_OPTIONS: VulnFindingStatus[] = [
  ...ALL_VULNERABILITY_STATUSES,
];

export const SORT_OPTIONS: VulnerabilitySortKey[] = [
  "severity",
  "cvss",
  "status",
  "discovered_at",
];

export interface VulnerabilitiesToolbarProps {
  search: string;
  onSearchChange: (value: string) => void;
  severity: VulnSeverity[];
  onSeverityChange: (value: VulnSeverity[]) => void;
  status: VulnFindingStatus[];
  onStatusChange: (value: VulnFindingStatus[]) => void;
  sort: VulnerabilitySortKey;
  onSortChange: (value: VulnerabilitySortKey) => void;
  order: SortOrder;
  onOrderChange: (value: SortOrder) => void;
  /** Trigger the vulnerability PDF report download (G2). */
  onDownloadPdf: () => void;
  /** True while the PDF is being generated/fetched — drives the loading label. */
  isPdfDownloading: boolean;
  /** Inline error from the last PDF download attempt, if any. */
  pdfError: Error | null;
  className?: string;
}

function selectedValues<T extends string>(
  event: React.ChangeEvent<HTMLSelectElement>,
): T[] {
  return Array.from(event.target.selectedOptions).map(
    (opt) => opt.value as T,
  );
}

export function VulnerabilitiesToolbar({
  search,
  onSearchChange,
  severity,
  onSeverityChange,
  status,
  onStatusChange,
  sort,
  onSortChange,
  order,
  onOrderChange,
  onDownloadPdf,
  isPdfDownloading,
  pdfError,
  className,
}: VulnerabilitiesToolbarProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className={cn(
        "flex flex-col gap-3 border-b bg-background px-4 py-3 lg:flex-row lg:items-end lg:gap-4",
        className,
      )}
      data-testid="vulnerabilities-toolbar"
    >
      <div className="flex-1">
        <label
          htmlFor="vulnerabilities-search"
          className="block text-xs font-medium text-muted-foreground"
        >
          {t("vulnerabilities.toolbar.search_label")}
        </label>
        <Input
          id="vulnerabilities-search"
          type="search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder={t("vulnerabilities.toolbar.search_placeholder")}
          data-testid="vulnerabilities-search"
          className="mt-1 h-9"
        />
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="vulnerabilities-severity-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("vulnerabilities.toolbar.severity_label")}
        </label>
        <select
          id="vulnerabilities-severity-filter"
          multiple
          size={1}
          value={severity}
          onChange={(event) =>
            onSeverityChange(selectedValues<VulnSeverity>(event))
          }
          className="mt-1 h-9 w-40 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="vulnerabilities-severity-filter"
        >
          {SEVERITY_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(`vulnerabilities.severity.${opt}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="vulnerabilities-status-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("vulnerabilities.toolbar.status_label")}
        </label>
        <select
          id="vulnerabilities-status-filter"
          multiple
          size={1}
          value={status}
          onChange={(event) =>
            onStatusChange(selectedValues<VulnFindingStatus>(event))
          }
          className="mt-1 h-9 w-44 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="vulnerabilities-status-filter"
        >
          {STATUS_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(`vulnerabilities.status.${opt}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="vulnerabilities-sort"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("vulnerabilities.toolbar.sort_label")}
        </label>
        <select
          id="vulnerabilities-sort"
          value={sort}
          onChange={(event) =>
            onSortChange(event.target.value as VulnerabilitySortKey)
          }
          className="mt-1 h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="vulnerabilities-sort"
        >
          {SORT_OPTIONS.map((key) => (
            <option key={key} value={key}>
              {t(`vulnerabilities.toolbar.sort_by_${key}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="vulnerabilities-order"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("vulnerabilities.toolbar.order_label")}
        </label>
        <select
          id="vulnerabilities-order"
          value={order}
          onChange={(event) => onOrderChange(event.target.value as SortOrder)}
          className="mt-1 h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="vulnerabilities-order"
        >
          <option value="asc">
            {t("vulnerabilities.toolbar.order_asc")}
          </option>
          <option value="desc">
            {t("vulnerabilities.toolbar.order_desc")}
          </option>
        </select>
      </div>

      <div className="flex flex-col lg:ml-auto">
        <span className="text-xs font-medium text-muted-foreground">
          {t("vulnerabilities.toolbar.report_label")}
        </span>
        <Button
          type="button"
          variant="default"
          size="sm"
          className="mt-1 h-9"
          onClick={onDownloadPdf}
          disabled={isPdfDownloading}
          data-testid="vuln-download-pdf"
        >
          {isPdfDownloading
            ? t("vulnerabilities.toolbar.download_pdf_generating")
            : t("vulnerabilities.toolbar.download_pdf")}
        </Button>
        {pdfError ? (
          <span
            className="mt-1 text-xs text-destructive"
            data-testid="vuln-download-pdf-error"
          >
            {pdfError.message}
          </span>
        ) : null}
      </div>
    </div>
  );
}
