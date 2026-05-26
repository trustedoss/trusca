import { AlertTriangle, ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  ComponentSeverity,
  LicenseCategoryName,
  ScanSummary,
} from "@/features/projects/api/projectDetailApi";
import { useProjectOverview } from "@/features/projects/api/useProjectOverview";
import { GateResultCard } from "@/features/projects/components/GateResultCard";
import { LicenseDistributionChart } from "@/features/projects/components/LicenseDistributionChart";
import { RecentScansTable } from "@/features/projects/components/RecentScansTable";
import { SeverityDistributionChart } from "@/features/projects/components/SeverityDistributionChart";
import { ProblemError } from "@/lib/problem";
import type { ProjectPublic } from "@/lib/projectsApi";

/**
 * OverviewTab — Phase 3 PR #10.
 *
 * Top section of the project detail page. Aggregates the project's risk
 * gauge, severity / license distributions, and the five most recent scans.
 * Wraps the `useProjectOverview` query with skeleton + RFC 7807 error UI.
 */

export interface OverviewTabProps {
  projectId: string;
  /**
   * The current project, when already resolved by the parent (the detail page
   * always fetches it for the breadcrumb / header). Threaded through so the
   * P2 #1 "Project info" card can render description / git_url / branch /
   * visibility without a second round-trip. Optional — when absent the card
   * is hidden (no double-fetch).
   */
  project?: ProjectPublic | null;
  /**
   * Called when a row in the recent-scans table is clicked AND the row's status
   * is queued/running. The parent uses it to re-open the live progress drawer
   * for that scan. Omit to render the table as read-only (no row affordance).
   */
  onSelectScan?: (scan: ScanSummary) => void;
  /**
   * W4-B #16 — Called when a row in the recent-scans table is clicked AND the
   * row's status is succeeded/failed/cancelled (the "result is final" lanes).
   * The parent pins that scan and jumps to the Components tab so the user
   * lands on the result the scan produced. Omit to keep all clicks routing
   * through `onSelectScan` (legacy behaviour).
   */
  onJumpToComponents?: (scan: ScanSummary) => void;
  /**
   * Pinned snapshot scan id (feature #28). When set, the risk gauge,
   * distributions, and gate card reflect that historical scan instead of the
   * latest succeeded one. Omit → latest (unchanged default).
   */
  scanId?: string;
}

export function OverviewTab({
  projectId,
  project,
  onSelectScan,
  onJumpToComponents,
  scanId,
}: OverviewTabProps) {
  const { t } = useTranslation("project_detail");
  const overview = useProjectOverview(projectId, scanId);
  const [, setSearchParams] = useSearchParams();

  // W4-B #16 — chart segment deep-link to the corresponding filtered list.
  // We write tab + the single facet directly to URL params (CSV-encoded so
  // it stays compatible with the multi-select convention used by the
  // Components / Vulnerabilities filters). `setTab` in the parent PDP drops
  // tab-scoped filters when LEAVING a tab, but ENTERING Components or
  // Vulnerabilities preserves `severity` / `license_category` (see PDP
  // line 196-218), so the deep-link survives unchanged.
  function jumpToVulnerabilitiesBySeverity(key: ComponentSeverity) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("tab", "vulnerabilities");
        next.set("severity", key);
        return next;
      },
      { replace: true },
    );
  }

  function jumpToLicensesByCategory(key: LicenseCategoryName) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        // W4-C #20 — Licenses tab was absorbed into the unified Compliance
        // tab. Land on its Licenses sub-view so the deep-link still surfaces
        // the license inventory filtered by category.
        next.set("tab", "compliance");
        next.set("cview", "licenses");
        next.set("license_category", key);
        return next;
      },
      { replace: true },
    );
  }

  // W4-B #16 — Recent Scans row click branches on scan.status:
  //   succeeded/failed/cancelled → pin the snapshot + jump to Components.
  //   queued/running             → re-open the live progress drawer.
  // The two callbacks are independent so a host that wires only one (legacy
  // tests, snapshots) doesn't lose the other lane silently.
  function handleScanRowClick(scan: ScanSummary) {
    const status = scan.status;
    if (status === "queued" || status === "running") {
      onSelectScan?.(scan);
      return;
    }
    // succeeded / failed / cancelled / any future "result-final" status.
    if (onJumpToComponents) {
      onJumpToComponents(scan);
    } else {
      // Fallback to onSelectScan so the row remains clickable in standalone
      // tests / harnesses that haven't wired the new prop yet.
      onSelectScan?.(scan);
    }
  }

  if (overview.isLoading) {
    return (
      <div
        data-testid="overview-loading"
        className="grid gap-4 p-6 md:grid-cols-2"
      >
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-32 w-full md:col-span-2" />
        <Skeleton className="h-48 w-full md:col-span-2" />
      </div>
    );
  }

  if (overview.isError) {
    const err = overview.error;
    const title =
      err instanceof ProblemError ? err.title : t("overview.errors.title");
    const detail =
      err instanceof ProblemError && err.detail
        ? err.detail
        : t("overview.errors.detail");
    return (
      <div className="p-6">
        <Alert variant="destructive" data-testid="overview-error">
          <AlertDescription>
            <div className="font-semibold">{title}</div>
            <div className="text-sm">{detail}</div>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const data = overview.data;
  if (!data) return null;

  return (
    <div
      data-testid="overview-tab"
      data-total-components={data.total_components}
      className="grid gap-4 p-6 md:grid-cols-2"
    >
      {/* P2 #1 — Project info card. Description / repo / branch / visibility
          come from the already-fetched ProjectPublic so this is a zero-cost
          addition. Card is hidden when the parent didn't pass `project` (e.g.
          standalone tests). Spans both grid columns so long descriptions or
          git URLs don't get truncated next to the risk gauge. */}
      {project ? (
        <Card className="md:col-span-2" data-testid="overview-info-card">
          <CardHeader>
            <CardTitle className="text-base">
              {t("overview.info_card.title", { defaultValue: "Project info" })}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-3 text-sm md:grid-cols-2">
              <div className="flex flex-col gap-1">
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  {t("overview.info_card.description", {
                    defaultValue: "Description",
                  })}
                </dt>
                <dd className="text-foreground">
                  {project.description ?? (
                    <span className="text-muted-foreground">
                      {t("overview.info_card.no_description", {
                        defaultValue: "No description set.",
                      })}
                    </span>
                  )}
                </dd>
              </div>
              <div className="flex flex-col gap-1">
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  {t("overview.info_card.git_url", {
                    defaultValue: "Repository",
                  })}
                </dt>
                <dd className="text-foreground">
                  {project.git_url ? (
                    <a
                      href={project.git_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 break-all font-mono text-xs text-foreground hover:underline"
                      data-testid="overview-info-git-url"
                    >
                      <span>{project.git_url}</span>
                      <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
                    </a>
                  ) : (
                    <span className="text-muted-foreground">
                      {t("overview.info_card.no_git_url", {
                        defaultValue: "No git URL configured.",
                      })}
                    </span>
                  )}
                </dd>
              </div>
              <div className="flex flex-col gap-1">
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  {t("overview.info_card.default_branch", {
                    defaultValue: "Default branch",
                  })}
                </dt>
                <dd className="font-mono text-xs text-foreground">
                  {project.default_branch ?? (
                    <span className="text-muted-foreground">—</span>
                  )}
                </dd>
              </div>
              <div className="flex flex-col gap-1">
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  {t("overview.info_card.visibility", {
                    defaultValue: "Visibility",
                  })}
                </dt>
                <dd>
                  <Badge
                    variant="outline"
                    className="font-mono text-xs"
                    data-testid="overview-info-visibility"
                    data-visibility={project.visibility}
                  >
                    {t(
                      `overview.info_card.visibility_value.${project.visibility}`,
                      { defaultValue: project.visibility },
                    )}
                  </Badge>
                  {project.has_git_credential ? (
                    <Badge
                      variant="outline"
                      className="ml-2 border-emerald-300 bg-emerald-50 text-xs text-emerald-800"
                      data-testid="overview-info-has-credential"
                    >
                      {t("overview.info_card.credential_configured", {
                        defaultValue: "Credential configured",
                      })}
                    </Badge>
                  ) : null}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      ) : null}

      {/* W4-B #16 — Risk Score card removed (the header's RiskGauge already
          covers the score), but the #35 Surface B empty-vuln caveat survives
          as a standalone alert at the top of Overview so users still see it
          when 0 CVEs means "no data". */}
      {data.total_components > 0 &&
      data.security_score === 0 &&
      data.vuln_data_available === false ? (
        <Alert
          className="border-amber-300 bg-amber-50 text-amber-900 md:col-span-2"
          data-testid="overview-vuln-data-unavailable"
        >
          <AlertTriangle className="h-4 w-4" aria-hidden />
          <AlertDescription>
            <span className="font-semibold">
              {t("overview.risk_card.vuln_data_empty_title")}
            </span>
            <span className="mt-1 block">
              {t("overview.risk_card.vuln_data_empty_body")}
            </span>
          </AlertDescription>
        </Alert>
      ) : null}

      <GateResultCard projectId={projectId} scanId={scanId} />

      <Card data-testid="overview-severity-card">
        <CardHeader>
          <CardTitle className="text-base">
            {t("overview.severity_card.title")}
          </CardTitle>
          <CardDescription>
            {t("overview.severity_card.subtitle")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {/* W4-B #16 — segment click deep-links to the filtered Vulnerabilities
              tab. Zero-count buckets stay non-interactive (chart guards). */}
          <SeverityDistributionChart
            distribution={data.severity_distribution}
            onSegmentClick={jumpToVulnerabilitiesBySeverity}
          />
        </CardContent>
      </Card>

      <Card className="md:col-span-2" data-testid="overview-license-card">
        <CardHeader>
          <CardTitle className="text-base">
            {t("overview.license_card.title")}
          </CardTitle>
          <CardDescription>
            {t("overview.license_card.subtitle")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {/* W4-B #16 — segment click deep-links to the filtered Licenses tab. */}
          <LicenseDistributionChart
            distribution={data.license_distribution}
            onSegmentClick={jumpToLicensesByCategory}
          />
        </CardContent>
      </Card>

      <Card className="md:col-span-2" data-testid="overview-recent-scans-card">
        <CardHeader>
          <CardTitle className="text-base">
            {t("overview.recent_scans.title")}
          </CardTitle>
          <CardDescription>
            {t("overview.recent_scans.subtitle")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {/* W4-B #16 — row click is status-aware (see handleScanRowClick). */}
          <RecentScansTable
            scans={data.recent_scans}
            onSelectScan={
              onSelectScan || onJumpToComponents
                ? handleScanRowClick
                : undefined
            }
          />
        </CardContent>
      </Card>
    </div>
  );
}
