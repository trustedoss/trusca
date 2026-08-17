// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { ScanLine } from "lucide-react";
import type { KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/EmptyState";
import RelativeTime from "@/components/RelativeTime";
import { Button } from "@/components/ui/button";
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
  /**
   * C3 - starts the first scan from the empty state.
   *
   * Omitted rather than disabled when the reader cannot scan: a demo
   * deployment, a historical release, or a project that has not loaded. The
   * empty state then says what it sees and offers nothing, which is better
   * than a button that refuses.
   */
  onScan?: () => void;
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
  onScan,
}: RecentScansTableProps) {
  const { t, i18n } = useTranslation("project_detail");
  const resolvedLocale = i18n.resolvedLanguage ?? i18n.language;

  if (scans.length === 0) {
    // C3 - the shared primitive rather than a line of muted text, because
    // this is the end of the first-scan path: a project has been registered
    // and nothing has looked at it yet. The description says what that means,
    // since "no scans" on its own reads as a fact about the project rather
    // than as the one step still missing.
    return (
      <EmptyState
        data-testid="recent-scans-empty"
        className={className}
        icon={<ScanLine />}
        title={t("overview.recent_scans.empty")}
        description={t("overview.recent_scans.empty_hint")}
        action={
          onScan ? (
            <Button size="sm" onClick={onScan} data-testid="recent-scans-scan">
              {t("overview.recent_scans.run_scan")}
            </Button>
          ) : undefined
        }
      />
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
                {/* B3: was a bare toLocaleString on a plain string, which
                    followed the browser rather than the app's language and
                    left the instant unreadable to anything parsing the DOM.
                    Through the component it is a <time dateTime> like every
                    other timestamp in the product. */}
                <RelativeTime
                  value={scan.started_at ?? scan.created_at}
                  display="absolute"
                  locale={resolvedLocale}
                  data-testid="recent-scan-started"
                />
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
