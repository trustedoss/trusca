import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { TeamScopedRole } from "@/features/projects/api/projectDetailApi";
import type {
  SortOrder,
  VulnFindingStatus,
  VulnSeverity,
  VulnerabilitySortKey,
} from "@/features/projects/api/vulnerabilitiesApi";
import { VexExportMenu } from "@/features/projects/components/VexExportMenu";
import { VexImportDialog } from "@/features/projects/components/VexImportDialog";
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
  "epss",
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
  /**
   * EPSS threshold (0–1) or `null` for "no threshold". Keeps findings with
   * `epss_score >= minEpss` and drops NULL-EPSS rows (v2.1).
   */
  minEpss: number | null;
  onMinEpssChange: (value: number | null) => void;
  /** Trigger the vulnerability PDF report download (G2). */
  onDownloadPdf: () => void;
  /** True while the PDF is being generated/fetched — drives the loading label. */
  isPdfDownloading: boolean;
  /** Inline error from the last PDF download attempt, if any. */
  pdfError: Error | null;
  /**
   * Keep only findings whose status was driven by a VEX import
   * (`analysis_source === "vex_import"`), v2.1 A3. Client-side narrowing of the
   * current page since the backend has no dedicated source filter yet.
   */
  vexSuppressedOnly: boolean;
  onVexSuppressedOnlyChange: (value: boolean) => void;
  /** Project id (for the VEX import/export controls). */
  projectId: string;
  /** Project name (download filename fallback). */
  projectName?: string | null;
  /** The actor's project-team-scoped role — gates the VEX import button. */
  projectRole?: TeamScopedRole;
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
  minEpss,
  onMinEpssChange,
  onDownloadPdf,
  isPdfDownloading,
  pdfError,
  vexSuppressedOnly,
  onVexSuppressedOnlyChange,
  projectId,
  projectName,
  projectRole = "developer",
  className,
}: VulnerabilitiesToolbarProps) {
  const { t } = useTranslation("project_detail");

  /**
   * Parse a free-text EPSS threshold. Empty → null (filter off). Numbers are
   * clamped to [0, 1] so a typo can't send an out-of-range value to the wire.
   */
  function handleMinEpssInput(raw: string) {
    const trimmed = raw.trim();
    if (trimmed.length === 0) {
      onMinEpssChange(null);
      return;
    }
    const parsed = Number.parseFloat(trimmed);
    if (!Number.isFinite(parsed)) return;
    const clamped = Math.min(1, Math.max(0, parsed));
    onMinEpssChange(clamped);
  }
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
              {t(`vulnerabilities.toolbar.sort_by_${key}`, {
                defaultValue: key === "epss" ? "EPSS" : undefined,
              })}
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

      <div className="flex flex-col">
        <label
          htmlFor="vulnerabilities-min-epss"
          className="text-xs font-medium text-muted-foreground"
          title={t("vulnerabilities.epss.tooltip", {
            defaultValue:
              "EPSS — probability this CVE is exploited in the wild within 30 days. Complements CVSS (severity).",
          })}
        >
          {t("vulnerabilities.filter.minEpss", {
            defaultValue: "EPSS ≥",
          })}
        </label>
        <div className="mt-1 flex items-center gap-1">
          <Input
            id="vulnerabilities-min-epss"
            type="number"
            inputMode="decimal"
            min={0}
            max={1}
            step={0.01}
            value={minEpss ?? ""}
            onChange={(event) => handleMinEpssInput(event.target.value)}
            placeholder={t("vulnerabilities.filter.minEpssPlaceholder", {
              defaultValue: "0.00–1.00",
            })}
            data-testid="vulnerabilities-min-epss"
            className="h-9 w-24 font-mono tabular-nums"
            aria-describedby="vulnerabilities-min-epss-hint"
          />
          {minEpss != null ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-9 px-2 text-xs"
              onClick={() => onMinEpssChange(null)}
              data-testid="vulnerabilities-min-epss-clear"
            >
              {t("vulnerabilities.filter.minEpssClear", {
                defaultValue: "Clear",
              })}
            </Button>
          ) : null}
        </div>
        <span id="vulnerabilities-min-epss-hint" className="sr-only">
          {t("vulnerabilities.filter.minEpssHint", {
            defaultValue:
              "Enter an EPSS probability between 0 and 1 to keep findings at or above it.",
          })}
        </span>
      </div>

      <div className="flex flex-col">
        <span className="text-xs font-medium text-muted-foreground">
          {t("vulnerabilities.vex.filter_label")}
        </span>
        <label className="mt-1 flex h-9 items-center gap-2 rounded-md border border-input bg-background px-2 text-sm">
          <input
            type="checkbox"
            checked={vexSuppressedOnly}
            onChange={(event) =>
              onVexSuppressedOnlyChange(event.target.checked)
            }
            data-testid="vulnerabilities-vex-suppressed-filter"
            className="h-4 w-4 rounded border-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <span>{t("vulnerabilities.vex.filter_suppressed")}</span>
        </label>
      </div>

      <VexExportMenu
        projectId={projectId}
        projectName={projectName}
        className="lg:ml-auto"
      />

      <VexImportDialog projectId={projectId} projectRole={projectRole} />

      <div className="flex flex-col">
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
