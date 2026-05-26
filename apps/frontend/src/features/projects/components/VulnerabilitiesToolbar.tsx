import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MultiSelect } from "@/components/ui/multi-select";
import type { TeamScopedRole } from "@/features/projects/api/projectDetailApi";
import type {
  ReachabilityFilter,
  VulnFindingStatus,
} from "@/features/projects/api/vulnerabilitiesApi";
import { VexExportMenu } from "@/features/projects/components/VexExportMenu";
import { VexImportDialog } from "@/features/projects/components/VexImportDialog";
import { ALL_VULNERABILITY_STATUSES } from "@/features/projects/lib/vulnerabilityTransitions";
import { cn } from "@/lib/utils";

/**
 * VulnerabilitiesToolbar — Phase 3 PR #11, updated in W4-B #19.
 *
 * Inline filter row above the virtualized vulnerabilities list. Mirrors the
 * shape of `ComponentsToolbar` (CLAUDE.md "디자인 시스템": filters appear
 * inline at the top of lists, no modal filter dialogs).
 *
 * W4-B #19 removed the severity / license MultiSelect drops and the
 * sort / order <select> controls. Severity + license are now driven by the
 * Overview chart deep-links (#16) and visualized via the standalone
 * `ActiveFilterChips` row in the parent tab. Sort is in the column headers
 * themselves (SortableColumnHeader primitive).
 *
 * What stays here: Search, Status MultiSelect, Reachability filter, EPSS
 * threshold, VEX-suppressed toggle, VEX export/import + PDF download.
 */

export const STATUS_OPTIONS: VulnFindingStatus[] = [
  ...ALL_VULNERABILITY_STATUSES,
];

/**
 * Reachability filter dropdown options (v2.3 r2). `""` is the "any" / off state;
 * the three tokens map to the backend's `?reachable=` parameter.
 */
export const REACHABLE_OPTIONS: ReachabilityFilter[] = [
  "true",
  "false",
  "unknown",
];

export interface VulnerabilitiesToolbarProps {
  search: string;
  onSearchChange: (value: string) => void;
  status: VulnFindingStatus[];
  onStatusChange: (value: VulnFindingStatus[]) => void;
  /**
   * EPSS threshold (0–1) or `null` for "no threshold". Keeps findings with
   * `epss_score >= minEpss` and drops NULL-EPSS rows (v2.1).
   */
  minEpss: number | null;
  onMinEpssChange: (value: number | null) => void;
  /**
   * Tri-state reachability filter (v2.3 r2): `"true"` / `"false"` / `"unknown"`
   * or `null` for "any". Backs the `?reachable=` query parameter.
   */
  reachable: ReachabilityFilter | null;
  onReachableChange: (value: ReachabilityFilter | null) => void;
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
  /**
   * Historical (read-only) snapshot mode (feature #28). When `true`, the VEX
   * import control is disabled (importing into an old snapshot would mutate the
   * current findings). The export + PDF report controls stay enabled — they are
   * read-only.
   */
  readOnly?: boolean;
  className?: string;
}

export function VulnerabilitiesToolbar({
  search,
  onSearchChange,
  status,
  onStatusChange,
  minEpss,
  onMinEpssChange,
  reachable,
  onReachableChange,
  onDownloadPdf,
  isPdfDownloading,
  pdfError,
  vexSuppressedOnly,
  onVexSuppressedOnlyChange,
  projectId,
  projectName,
  projectRole = "developer",
  readOnly = false,
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
          htmlFor="vulnerabilities-status-filter"
          className="text-xs font-medium text-muted-foreground"
        >
          {t("vulnerabilities.toolbar.status_label")}
        </label>
        <MultiSelect
          id="vulnerabilities-status-filter"
          testId="vulnerabilities-status-filter"
          className="w-44"
          label={t("vulnerabilities.toolbar.status_label")}
          options={STATUS_OPTIONS.map((opt) => ({
            value: opt,
            label: t(`vulnerabilities.status.${opt}`),
          }))}
          selected={status}
          onChange={(next) => onStatusChange(next as VulnFindingStatus[])}
        />
      </div>

      <div className="flex flex-col">
        <label
          htmlFor="vulnerabilities-reachable-filter"
          className="text-xs font-medium text-muted-foreground"
          title={t("vulnerabilities.reachability.tooltip_filter")}
        >
          {t("vulnerabilities.toolbar.reachable_label")}
        </label>
        <select
          id="vulnerabilities-reachable-filter"
          value={reachable ?? ""}
          onChange={(event) =>
            onReachableChange(
              event.target.value === ""
                ? null
                : (event.target.value as ReachabilityFilter),
            )
          }
          className="mt-1 h-9 w-40 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="vulnerabilities-reachable-filter"
        >
          <option value="">
            {t("vulnerabilities.toolbar.reachable_any")}
          </option>
          {REACHABLE_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(`vulnerabilities.toolbar.reachable_option.${opt}`)}
            </option>
          ))}
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

      <VexImportDialog
        projectId={projectId}
        projectRole={projectRole}
        readOnly={readOnly}
      />

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
