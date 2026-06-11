import { ChevronRight, GitCompare, ShieldCheck, ShieldX } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useReleases } from "@/features/projects/api/useReleases";
import type {
  ReleaseGateStatus,
  ReleaseSeveritySummary,
  ReleaseSnapshot,
} from "@/features/projects/api/releasesApi";
import { releaseLabel } from "@/features/projects/lib/releaseLabel";
import { ProblemError } from "@/lib/problem";
import RelativeTime from "@/components/RelativeTime";
import { cn } from "@/lib/utils";

/**
 * ReleasesTab — feature #28 Phase 1 (release snapshot viewing).
 *
 * A release is one *succeeded* scan. This tab is the project's release history,
 * newest-first, rendered as a compact 40px-row table:
 *
 *   Release | Date | Risk | Severity (C/H/M/L) | Gate | [View snapshot]
 *
 * Clicking "View snapshot" pins that scan via `?scan=<scan_id>` and navigates
 * to the Overview tab so the user sees the full snapshot picture (the parent
 * owns that navigation via `onViewSnapshot`).
 *
 * CLAUDE.md "디자인 시스템": compact density, skeleton loading (no spinner),
 * inline empty/error states, risk colors paired with a label/icon (never
 * color-only), design tokens only (no hex).
 */

const PAGE_SIZE = 50;

/**
 * Risk score → token + label, mirroring RiskGauge's thresholds so the column
 * color matches the gauge a viewer would see after opening the snapshot.
 */
function riskToneClass(score: number | null): string {
  if (score == null) return "text-muted-foreground";
  if (score >= 75) return "text-risk-critical";
  if (score >= 50) return "text-risk-high";
  if (score >= 25) return "text-risk-medium";
  if (score > 0) return "text-risk-low";
  return "text-risk-info";
}

/** Severity bucket → text token for the C/H/M/L chips. */
const SEVERITY_TOKEN: Record<keyof ReleaseSeveritySummary, string> = {
  critical: "text-risk-critical",
  high: "text-risk-high",
  medium: "text-risk-medium",
  low: "text-risk-low",
};

const SEVERITY_ORDER: Array<keyof ReleaseSeveritySummary> = [
  "critical",
  "high",
  "medium",
  "low",
];

export interface ReleasesTabProps {
  projectId: string;
  /**
   * Pin a snapshot and jump to its full view. The parent sets `?scan=<scanId>`
   * (preserving other params) and navigates to the Overview tab.
   */
  onViewSnapshot: (scanId: string) => void;
}

export function ReleasesTab({ projectId, onViewSnapshot }: ReleasesTabProps) {
  const { t, i18n } = useTranslation("project_detail");
  const locale = i18n.language;
  const navigate = useNavigate();

  const releases = useReleases(projectId, { page: 1, size: PAGE_SIZE });

  const items = releases.data?.items ?? [];
  const total = releases.data?.total ?? 0;

  // Compare needs two snapshots. Default target = newest, base = next down,
  // matching the "Compare to the previous release" mental model. The button is
  // disabled (with a hint) until the project has at least two releases.
  const canCompare = items.length >= 2;
  function handleCompare() {
    if (!canCompare) return;
    const target = items[0].scan_id;
    const base = items[1].scan_id;
    navigate(
      `/projects/${projectId}/compare?base=${encodeURIComponent(
        base,
      )}&target=${encodeURIComponent(target)}`,
    );
  }

  return (
    <div className="p-6" data-testid="releases-tab">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
          <CardTitle className="text-base">{t("releases.title")}</CardTitle>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={handleCompare}
            disabled={!canCompare}
            title={!canCompare ? t("compare.open_disabled_hint") : undefined}
            aria-label={t("compare.open_button_aria")}
            data-testid="releases-compare-button"
          >
            <GitCompare className="h-3.5 w-3.5" aria-hidden />
            {t("compare.open_button")}
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <p
            className="text-xs text-muted-foreground"
            data-testid="releases-subtitle"
          >
            {t("releases.subtitle")}
          </p>

          {releases.isError ? (
            <Alert variant="destructive" data-testid="releases-error">
              <AlertDescription>
                {releases.error instanceof ProblemError
                  ? releases.error.detail
                  : t("releases.errors.load_failed")}
              </AlertDescription>
            </Alert>
          ) : null}

          {releases.isLoading ? (
            <div
              className="flex flex-col gap-2"
              data-testid="releases-loading"
            >
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : null}

          {!releases.isLoading && !releases.isError && items.length === 0 ? (
            <div
              className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground"
              data-testid="releases-empty"
            >
              {t("releases.empty")}
            </div>
          ) : null}

          {!releases.isLoading && !releases.isError && items.length > 0 ? (
            <div
              className="overflow-x-auto"
              data-testid="releases-table"
              data-total={total}
              data-loaded={items.length}
            >
              <table className="w-full text-sm">
                <thead className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-medium">
                      {t("releases.col.release")}
                    </th>
                    <th className="px-3 py-2 font-medium">
                      {t("releases.col.date")}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t("releases.col.risk")}
                    </th>
                    <th className="px-3 py-2 font-medium">
                      {t("releases.col.severity")}
                    </th>
                    <th className="px-3 py-2 font-medium">
                      {t("releases.col.gate")}
                    </th>
                    <th className="w-8 px-3 py-2" aria-hidden></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <ReleaseRow
                      key={item.scan_id}
                      release={item}
                      locale={locale}
                      onView={() => onViewSnapshot(item.scan_id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

interface ReleaseRowProps {
  release: ReleaseSnapshot;
  locale: string;
  onView: () => void;
}

function ReleaseRow({ release, locale, onView }: ReleaseRowProps) {
  const { t } = useTranslation("project_detail");
  const label = releaseLabel(release, locale);
  const hasReleaseName =
    release.release != null && release.release.trim().length > 0;

  return (
    <tr
      data-testid="release-row"
      data-scan-id={release.scan_id}
      role="button"
      tabIndex={0}
      onClick={onView}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onView();
        }
      }}
      aria-label={t("releases.view_snapshot_aria", { label })}
      className="cursor-pointer border-b last:border-b-0 transition-colors duration-fast ease-out-soft hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      style={{ height: "var(--table-row)" }}
    >
      <td className="px-3 py-2">
        <span
          className={cn("font-medium", !hasReleaseName && "font-mono text-xs")}
          data-testid="release-row-label"
        >
          {label}
        </span>
      </td>
      <td
        className="px-3 py-2 text-xs text-muted-foreground"
        data-testid="release-row-date"
      >
        <RelativeTime value={release.created_at} locale={locale} />
      </td>
      <td
        className={cn(
          "px-3 py-2 text-right font-mono tabular-nums",
          riskToneClass(release.risk_score),
        )}
        data-testid="release-row-risk"
      >
        {release.risk_score != null ? release.risk_score.toFixed(0) : "—"}
      </td>
      <td className="px-3 py-2" data-testid="release-row-severity">
        <SeverityCounts summary={release.severity_summary} />
      </td>
      <td className="px-3 py-2" data-testid="release-row-gate">
        <GateBadge status={release.gate_status} />
      </td>
      <td className="px-3 py-2 text-right text-muted-foreground" aria-hidden>
        <ChevronRight className="ml-auto h-4 w-4" data-testid="release-row-view" />
      </td>
    </tr>
  );
}

function SeverityCounts({ summary }: { summary: ReleaseSeveritySummary }) {
  const { t } = useTranslation("project_detail");
  const nonZero = SEVERITY_ORDER.filter((key) => summary[key] > 0);

  if (nonZero.length === 0) {
    return (
      <span
        className="text-xs text-muted-foreground"
        data-testid="release-row-severity-none"
      >
        {t("releases.severity_none")}
      </span>
    );
  }

  return (
    <span className="flex flex-wrap items-center gap-1.5 font-mono text-xs tabular-nums">
      {nonZero.map((key) => (
        <span
          key={key}
          className={cn("inline-flex items-center", SEVERITY_TOKEN[key])}
          data-testid={`release-severity-${key}`}
          title={t(`severity.${key}`)}
        >
          {t(`releases.severity_abbr.${key}`)}
          {summary[key]}
        </span>
      ))}
    </span>
  );
}

function GateBadge({ status }: { status: ReleaseGateStatus | null }) {
  const { t } = useTranslation("project_detail");
  if (status == null) {
    return (
      <span
        className="text-xs text-muted-foreground"
        data-testid="release-gate-none"
        data-gate="none"
      >
        —
      </span>
    );
  }
  if (status === "pass") {
    return (
      <Badge tone="success" className="gap-1.5" data-testid="release-gate-pass">
        <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
        {t("releases.gate.pass")}
      </Badge>
    );
  }
  return (
    <Badge variant="destructive" className="gap-1.5" data-testid="release-gate-fail">
      <ShieldX className="h-3.5 w-3.5" aria-hidden />
      {t("releases.gate.fail")}
    </Badge>
  );
}
