import type { KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import type { ScanSummary } from "@/features/projects/api/projectDetailApi";
import { cn } from "@/lib/utils";

/**
 * RecentScansTable — Phase 3 PR #10.
 *
 * Compact (40px row) table of the project's last five scans. Shows
 * started_at, status, duration and a localized result label. CLAUDE.md
 * "디자인 시스템" — compact density, no modals, color always paired with text.
 */

export interface RecentScansTableProps {
  scans: ScanSummary[];
  className?: string;
  /**
   * When provided, each row becomes an activatable control (click / Enter /
   * Space) that re-opens the live progress drawer for that scan. Omit to keep
   * the table read-only.
   */
  onSelectScan?: (scan: ScanSummary) => void;
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function formatDuration(
  started: string | null,
  completed: string | null,
): string {
  if (!started || !completed) return "—";
  const ms = new Date(completed).getTime() - new Date(started).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `${minutes}m ${remainder}s`;
}

const STATUS_TONE: Record<string, string> = {
  succeeded: "bg-emerald-500",
  running: "bg-risk-low",
  queued: "bg-risk-info",
  failed: "bg-risk-critical",
  cancelled: "bg-risk-high",
};

export function RecentScansTable({
  scans,
  className,
  onSelectScan,
}: RecentScansTableProps) {
  const { t } = useTranslation("project_detail");

  if (scans.length === 0) {
    return (
      <div
        data-testid="recent-scans-empty"
        className={cn("text-sm text-muted-foreground", className)}
      >
        {t("overview.recent_scans.empty")}
      </div>
    );
  }

  return (
    <div
      data-testid="recent-scans-table"
      className={cn("overflow-x-auto", className)}
    >
      <table className="w-full text-sm">
        <thead className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">
              {t("overview.recent_scans.col_started")}
            </th>
            <th className="px-3 py-2 font-medium">
              {t("overview.recent_scans.col_version")}
            </th>
            <th className="px-3 py-2 font-medium">
              {t("overview.recent_scans.col_status")}
            </th>
            <th className="px-3 py-2 font-medium">
              {t("overview.recent_scans.col_duration")}
            </th>
            <th className="px-3 py-2 font-medium">
              {t("overview.recent_scans.col_kind")}
            </th>
          </tr>
        </thead>
        <tbody>
          {scans.map((scan) => (
            <tr
              key={scan.id}
              data-testid="recent-scan-row"
              data-scan-id={scan.id}
              data-status={scan.status}
              className={cn(
                "border-b last:border-b-0",
                onSelectScan &&
                  "cursor-pointer transition-colors duration-fast ease-out-soft hover:bg-accent/40 focus-visible:bg-accent/40 focus-visible:outline-none",
              )}
              style={{ height: "var(--table-row)" }}
              {...(onSelectScan
                ? {
                    role: "button",
                    tabIndex: 0,
                    "aria-label": t("overview.recent_scans.reopen_aria", {
                      defaultValue: "View progress for this scan",
                    }),
                    onClick: () => onSelectScan(scan),
                    onKeyDown: (event: KeyboardEvent<HTMLTableRowElement>) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onSelectScan(scan);
                      }
                    },
                  }
                : {})}
            >
              <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                {formatDateTime(scan.started_at ?? scan.created_at)}
              </td>
              <td
                className="px-3 py-2 font-mono text-xs"
                data-testid="recent-scan-version"
              >
                {scan.release ?? (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td className="px-3 py-2">
                <span className="inline-flex items-center gap-2">
                  <span
                    aria-hidden
                    className={cn(
                      "inline-block h-1.5 w-1.5 rounded-full",
                      STATUS_TONE[scan.status] ?? "bg-risk-info",
                    )}
                  />
                  <span>
                    {t(`overview.recent_scans.status.${scan.status}`, {
                      defaultValue: scan.status,
                    })}
                  </span>
                </span>
              </td>
              <td className="px-3 py-2 tabular-nums">
                {formatDuration(scan.started_at, scan.completed_at)}
              </td>
              <td className="px-3 py-2">
                {t(`overview.recent_scans.kind.${scan.kind}`, {
                  defaultValue: scan.kind,
                })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
