/**
 * DashboardPage — W9-#50 (audit D1-001).
 *
 * Dedicated portfolio overview rendered at "/". Replaces the previous
 * `Navigate to="/projects"` redirect, which was the lone gap vs. every
 * audited competitor (BD Polaris, Snyk, Sonatype, Mend, Datadog CSM all
 * open on a dashboard, not a list).
 *
 * Structure (top → bottom, mirrors `docs/ux/competitive-audit-2026-05-27.md`
 * §5 + screenshots `bd-polaris-dashboard-filters.png` +
 * `csm-vm-dashboard.png`):
 *
 *   1. Header                  — "Dashboard" h1 + relative-time "last updated"
 *   2. KPI cards (grid-cols-4) — active projects, open vulns, pending
 *                                approvals, last scan. Each card carries an
 *                                inline "view all" link (W11-G EmptyState
 *                                tokens, no hex literals).
 *   3. Distribution charts     — vuln severity (by project) + license
 *      (grid-cols-2)            classification (by project). Both reuse the
 *                                W4-B chart primitives; segment click deep-
 *                                links into /projects?severity=… (legacy
 *                                ProjectListPage already honours these query
 *                                params, no new behavior needed here).
 *   4. Recent scans + activity — scans = `useScans` slice (top-10), activity
 *      (grid-cols-3)            = notifications slice (top-10).
 *
 * Server state lives in TanStack Query exclusively. There is NO backend
 * dashboard aggregation endpoint — we fan out three existing list endpoints
 * (`/v1/projects`, `/v1/scans`, `/v1/approvals`) and do counts client-side.
 * That is intentionally cheap for the demo SaaS scale (≤100 projects per
 * team); a dedicated `/v1/dashboard/summary` endpoint is captured as a
 * follow-up in the PR body.
 */
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ClipboardCheck,
  FolderOpen,
  ScanLine,
  ShieldAlert,
  type LucideIcon,
} from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  MiniSeverityBar,
  type SeverityCounts,
} from "@/features/dashboard/MiniSeverityBar";
import { ScanStatusPill } from "@/features/dashboard/ScanStatusPill";
import { LicenseDistributionChart } from "@/features/projects/components/LicenseDistributionChart";
import { SeverityDistributionChart } from "@/features/projects/components/SeverityDistributionChart";
import { useScans } from "@/features/scans/useScans";
import { useDemoMode } from "@/hooks/useDemoMode";
import { listApprovals } from "@/lib/approvalsApi";
import {
  listProjects,
  type ProjectPublic,
  type ScanPublic,
} from "@/lib/projectsApi";
import { formatRelativeToNow } from "@/lib/relativeTime";
import RelativeTime from "@/components/RelativeTime";

// Project list page-size ceiling matches ProjectListPage (size=100, the
// backend `GET /v1/projects` cap). Counts are computed off this slice, so
// at ≥100 projects the dashboard under-reports — a known limitation called
// out in the PR body as a follow-up (backend aggregation endpoint).
const PROJECT_PAGE_SIZE = 100;
const RECENT_SCANS_LIMIT = 10;

// ---------------------------------------------------------------------------
// KPI card primitives
// ---------------------------------------------------------------------------

interface KpiCardProps {
  testId: string;
  icon: LucideIcon;
  label: string;
  /**
   * Primary metric — a pre-formatted number/short string, or a node (e.g.
   * `<RelativeTime>` for the "last scan" stamp so it carries an absolute-time
   * tooltip).
   */
  value: React.ReactNode;
  /**
   * Optional supporting line (e.g. project name for "last scan", relative
   * time, "never scanned" fallback). Already translated.
   */
  hint?: string;
  /** Optional "view all" deep link rendered as a tertiary button. */
  link?: { to: string; label: string };
  /** Optional decoration slot (severity mini-bar etc.). */
  decoration?: React.ReactNode;
  loading?: boolean;
}

function KpiCard({
  testId,
  icon: Icon,
  label,
  value,
  hint,
  link,
  decoration,
  loading,
}: KpiCardProps) {
  return (
    <Card
      data-testid={testId}
      className="flex h-full flex-col"
      data-loading={loading ? "true" : "false"}
    >
      <CardHeader className="px-5 py-4 pb-2">
        <div className="flex items-center justify-between gap-3">
          <CardDescription className="flex items-center gap-2 text-xs uppercase tracking-wide">
            <Icon aria-hidden className="h-4 w-4 text-muted-foreground" />
            <span>{label}</span>
          </CardDescription>
          {link ? (
            <Link
              to={link.to}
              className="text-xs font-medium text-muted-foreground transition-colors duration-fast ease-out-soft hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              data-testid={`${testId}-view-all`}
            >
              {link.label}
            </Link>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-1 px-5 pb-4 pt-0">
        {loading ? (
          <Skeleton className="h-7 w-24" />
        ) : (
          <CardTitle
            className="font-mono text-2xl tabular-nums"
            data-testid={`${testId}-value`}
          >
            {value}
          </CardTitle>
        )}
        {hint ? (
          <p
            className="truncate text-xs text-muted-foreground"
            data-testid={`${testId}-hint`}
            title={hint}
          >
            {hint}
          </p>
        ) : null}
        {decoration ? <div className="pt-1">{decoration}</div> : null}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Aggregation helpers — pure functions, easy to unit test. `SeverityCounts`
// is imported from MiniSeverityBar so the two surfaces share one type.
// ---------------------------------------------------------------------------

interface LicenseCounts {
  forbidden: number;
  conditional: number;
  allowed: number;
  unknown: number;
}

/**
 * Per-project worst-bucket aggregation. Mirrors the
 * `severityDistByProject` calc the ProjectListPage already does on its
 * distribution card, so segment-click deep-link semantics match.
 */
export function aggregateSeverityByProject(
  projects: readonly ProjectPublic[],
): SeverityCounts {
  const counts: SeverityCounts = {
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
    info: 0,
    none: 0,
  };
  for (const p of projects) {
    const s = p.severity_summary;
    if (!s) continue;
    const worst =
      s.critical > 0
        ? "critical"
        : s.high > 0
          ? "high"
          : s.medium > 0
            ? "medium"
            : s.low > 0
              ? "low"
              : null;
    if (worst !== null) counts[worst] += 1;
  }
  return counts;
}

export function aggregateLicenseByProject(
  projects: readonly ProjectPublic[],
): LicenseCounts {
  const counts: LicenseCounts = {
    forbidden: 0,
    conditional: 0,
    allowed: 0,
    unknown: 0,
  };
  for (const p of projects) {
    const l = p.license_category_summary;
    if (!l) continue;
    const worst =
      l.forbidden > 0
        ? "forbidden"
        : l.conditional > 0
          ? "conditional"
          : l.allowed > 0
            ? "allowed"
            : l.unknown > 0
              ? "unknown"
              : null;
    if (worst !== null) counts[worst] += 1;
  }
  return counts;
}

/**
 * Total open vulnerability count across the loaded project slice. Sums the
 * latest-succeeded-scan severity buckets. Returns 0 when no project has a
 * succeeded scan yet.
 */
export function aggregateOpenVulnerabilities(
  projects: readonly ProjectPublic[],
): number {
  let total = 0;
  for (const p of projects) {
    const s = p.severity_summary;
    if (!s) continue;
    total += s.critical + s.high + s.medium + s.low;
  }
  return total;
}

/**
 * Pick the most-recent scan attempt across the project slice. `last_scan_at`
 * is null on never-scanned projects; we ignore those.
 */
export function pickLastScannedProject(
  projects: readonly ProjectPublic[],
): ProjectPublic | null {
  let winner: ProjectPublic | null = null;
  for (const p of projects) {
    if (p.last_scan_at == null) continue;
    if (winner == null || p.last_scan_at > winner.last_scan_at!) {
      winner = p;
    }
  }
  return winner;
}

function durationSeconds(scan: ScanPublic): number | null {
  if (!scan.started_at) return null;
  const start = Date.parse(scan.started_at);
  const end = scan.completed_at ? Date.parse(scan.completed_at) : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return Math.max(0, Math.round((end - start) / 1000));
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DashboardPage() {
  const { t, i18n } = useTranslation("dashboard");
  const { demoReadOnly } = useDemoMode();
  const locale = i18n.resolvedLanguage;

  // Projects slice — drives the active-projects KPI, the open-vuln total,
  // the last-scan KPI, and both distribution charts. Same query shape /
  // size as ProjectListPage so the TanStack cache lights up immediately on
  // navigation between the two surfaces.
  const projectsQuery = useQuery({
    queryKey: ["projects", { page: 1, size: PROJECT_PAGE_SIZE }],
    queryFn: () => listProjects({ page: 1, size: PROJECT_PAGE_SIZE }),
  });

  // Recent scans — newest first. The `useScans` hook handles the 30 s
  // refetch so the table stays roughly live. We slice to 10 client-side
  // because the hook defaults page-size to 20.
  const scansQuery = useScans({ page: 1, size: RECENT_SCANS_LIMIT });

  // Pending-approvals count — we only need the total (not the rows), so we
  // request page_size=1 and read `.total`. Stale time follows the hook's
  // default.
  const approvalsQuery = useQuery({
    queryKey: ["approvals", { status: "pending", page: 1, page_size: 1 }],
    queryFn: () => listApprovals({ status: "pending", page: 1, page_size: 1 }),
    staleTime: 30_000,
  });

  // Stable reference so dependent useMemo blocks below don't re-fire when
  // the surrounding render is unrelated. `projectsQuery.data` itself is
  // already memoised by TanStack Query — we just need to coalesce the
  // `?? []` fallback into the same identity.
  const projects = useMemo(
    () => projectsQuery.data?.items ?? [],
    [projectsQuery.data],
  );
  const activeProjectCount = useMemo(
    () => projects.filter((p) => p.archived_at == null).length,
    [projects],
  );
  const severityByProject = useMemo(
    () => aggregateSeverityByProject(projects),
    [projects],
  );
  const licenseByProject = useMemo(
    () => aggregateLicenseByProject(projects),
    [projects],
  );
  const openVulns = useMemo(
    () => aggregateOpenVulnerabilities(projects),
    [projects],
  );
  const lastScannedProject = useMemo(
    () => pickLastScannedProject(projects),
    [projects],
  );

  const pendingApprovalsCount = approvalsQuery.data?.total ?? 0;
  const recentScans = scansQuery.data?.items.slice(0, RECENT_SCANS_LIMIT) ?? [];

  const isAnyLoading =
    projectsQuery.isLoading || scansQuery.isLoading || approvalsQuery.isLoading;
  const isAnyError =
    projectsQuery.isError || scansQuery.isError || approvalsQuery.isError;

  // M-18 — retry only the queries that actually failed; the healthy ones
  // keep their cache and re-render instantly once the failed ones recover.
  function retryFailedQueries() {
    if (projectsQuery.isError) void projectsQuery.refetch();
    if (scansQuery.isError) void scansQuery.refetch();
    if (approvalsQuery.isError) void approvalsQuery.refetch();
  }
  const projectsLoaded = !projectsQuery.isLoading && !projectsQuery.isError;
  const hasNoProjects = projectsLoaded && projects.length === 0;

  // Last-updated stamp anchored to the moment the heaviest query (projects)
  // last resolved. Falls back to "—" before the first resolution so we don't
  // flash a stale absolute clock.
  const lastUpdatedIso =
    projectsQuery.dataUpdatedAt > 0
      ? new Date(projectsQuery.dataUpdatedAt).toISOString()
      : null;
  const lastUpdatedLabel = lastUpdatedIso
    ? formatRelativeToNow(lastUpdatedIso, locale)
    : t("kpi.no_data");

  return (
    <div
      className="flex flex-col bg-background text-foreground"
      data-testid="dashboard-page"
    >
      {/* Header — 48 px, parity with ProjectListPage / ScansPage. */}
      <header
        className="flex shrink-0 items-center justify-between border-b bg-background px-6"
        style={{ height: "var(--layout-header)" }}
      >
        <h1 className="text-base font-semibold tracking-tight">
          {t("heading")}
        </h1>
        <p
          className="text-xs text-muted-foreground"
          data-testid="dashboard-last-updated"
        >
          {t("last_updated", { time: lastUpdatedLabel })}
        </p>
      </header>

      {/* M-18 — a load failure REPLACES the KPI/chart/recent body instead of
          stacking an alert above zero-value tiles (zeros read as a healthy
          portfolio). `isAnyError` is already a composite of the three fan-out
          queries and every section below draws on at least one of them, so a
          full-body swap is the consistent granularity; the Retry button only
          refetches the queries that actually failed. */}
      {isAnyError ? (
        <div className="px-6 py-8" data-testid="dashboard-error-wrapper">
          <EmptyState
            data-testid="dashboard-error"
            icon={<AlertTriangle />}
            title={t("error.load_failed")}
            description={t("error.retry_hint")}
            action={
              <Button
                variant="outline"
                onClick={retryFailedQueries}
                data-testid="dashboard-error-retry"
              >
                {t("error.retry")}
              </Button>
            }
          />
        </div>
      ) : hasNoProjects ? (
        <div className="px-6 py-8" data-testid="dashboard-empty-wrapper">
          <EmptyState
            data-testid="dashboard-empty"
            icon={<FolderOpen />}
            title={t("empty.title")}
            description={t("empty.description")}
            action={
              demoReadOnly ? (
                <Button disabled title={t("empty.cta")}>
                  {t("empty.cta")}
                </Button>
              ) : (
                <Button asChild data-testid="dashboard-empty-cta">
                  <Link to="/projects/new">{t("empty.cta")}</Link>
                </Button>
              )
            }
          />
        </div>
      ) : (
        <>
          {/* KPI grid — 4 cards on lg+, 2 on md, 1 on mobile. */}
          <section
            className="grid gap-4 px-6 py-4 sm:grid-cols-2 lg:grid-cols-4"
            data-testid="dashboard-kpi-grid"
          >
            <KpiCard
              testId="dashboard-kpi-projects"
              icon={FolderOpen}
              label={t("kpi.active_projects")}
              value={String(activeProjectCount)}
              link={{ to: "/projects", label: t("kpi.view_all") }}
              loading={projectsQuery.isLoading}
            />
            <KpiCard
              testId="dashboard-kpi-vulns"
              icon={ShieldAlert}
              label={t("kpi.open_vulnerabilities")}
              value={String(openVulns)}
              link={{ to: "/projects", label: t("kpi.view_all") }}
              loading={projectsQuery.isLoading}
              decoration={
                openVulns > 0 ? (
                  <MiniSeverityBar counts={severityByProject} />
                ) : null
              }
            />
            <KpiCard
              testId="dashboard-kpi-approvals"
              icon={ClipboardCheck}
              label={t("kpi.pending_approvals")}
              value={String(pendingApprovalsCount)}
              link={{ to: "/approvals", label: t("kpi.view_all") }}
              loading={approvalsQuery.isLoading}
            />
            <KpiCard
              testId="dashboard-kpi-last-scan"
              icon={ScanLine}
              label={t("kpi.last_scan")}
              value={
                lastScannedProject?.last_scan_at ? (
                  <RelativeTime
                    value={lastScannedProject.last_scan_at}
                    locale={locale}
                  />
                ) : (
                  t("kpi.never_scanned")
                )
              }
              hint={lastScannedProject?.name ?? undefined}
              link={
                lastScannedProject
                  ? {
                      to: `/projects/${lastScannedProject.id}`,
                      label: t("kpi.view_all"),
                    }
                  : undefined
              }
              loading={projectsQuery.isLoading}
            />
          </section>

          {/* Distribution charts — 2 columns on lg+. */}
          <section
            className="grid gap-4 px-6 pb-4 lg:grid-cols-2"
            data-testid="dashboard-charts"
          >
            <Card data-testid="dashboard-severity-card">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">
                  {t("chart.severity_heading")}
                </CardTitle>
                <CardDescription>
                  {t("chart.subtitle_severity")}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {projectsQuery.isLoading ? (
                  <Skeleton className="h-20 w-full" />
                ) : (
                  <SeverityDistributionChart
                    distribution={severityByProject}
                    onSegmentClick={(key) => {
                      if (key === "info" || key === "none") return;
                      window.location.assign(
                        `/projects?severity=${encodeURIComponent(key)}`,
                      );
                    }}
                  />
                )}
              </CardContent>
            </Card>
            <Card data-testid="dashboard-license-card">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">
                  {t("chart.license_heading")}
                </CardTitle>
                <CardDescription>
                  {t("chart.subtitle_license")}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {projectsQuery.isLoading ? (
                  <Skeleton className="h-20 w-full" />
                ) : (
                  <LicenseDistributionChart
                    distribution={licenseByProject}
                    onSegmentClick={(key) => {
                      window.location.assign(
                        `/projects?license_category=${encodeURIComponent(key)}`,
                      );
                    }}
                  />
                )}
              </CardContent>
            </Card>
          </section>

          {/* Recent scans (col-span-2) + recent activity placeholder. */}
          <section
            className="grid gap-4 px-6 pb-6 lg:grid-cols-3"
            data-testid="dashboard-recent"
          >
            <Card
              className="lg:col-span-2"
              data-testid="dashboard-recent-scans-card"
            >
              <CardHeader className="flex flex-row items-baseline justify-between pb-3">
                <CardTitle className="text-base">
                  {t("recent_scans.heading")}
                </CardTitle>
                <Button
                  asChild
                  variant="ghost"
                  size="sm"
                  data-testid="dashboard-recent-scans-view-all"
                >
                  <Link to="/scans">{t("recent_scans.view_all")}</Link>
                </Button>
              </CardHeader>
              <CardContent className="p-0">
                {scansQuery.isLoading ? (
                  <div className="space-y-2 px-6 py-4">
                    {Array.from({ length: 5 }).map((_, i) => (
                      <Skeleton key={i} className="h-8 w-full" />
                    ))}
                  </div>
                ) : recentScans.length === 0 ? (
                  <EmptyState
                    data-testid="dashboard-recent-scans-empty"
                    icon={<ScanLine />}
                    title={t("recent_scans.empty")}
                  />
                ) : (
                  <table
                    className="w-full text-sm"
                    data-testid="dashboard-recent-scans-table"
                  >
                    <thead className="bg-muted/40">
                      <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                        <th className="px-6 py-2">
                          {t("recent_scans.column.project")}
                        </th>
                        <th className="px-3 py-2">
                          {t("recent_scans.column.kind")}
                        </th>
                        <th className="px-3 py-2">
                          {t("recent_scans.column.status")}
                        </th>
                        <th className="px-3 py-2">
                          {t("recent_scans.column.started")}
                        </th>
                        <th className="px-3 py-2 text-right">
                          {t("recent_scans.column.duration")}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentScans.map((scan) => {
                        const dur = durationSeconds(scan);
                        return (
                          <tr
                            key={scan.id}
                            data-testid="dashboard-recent-scan-row"
                            data-scan-id={scan.id}
                            className="border-b transition-colors duration-fast ease-out-soft hover:bg-accent/40"
                            style={{ height: "var(--table-row)" }}
                          >
                            <td className="px-6 text-xs">
                              {scan.project_name ? (
                                <Link
                                  to={`/projects/${scan.project_id}`}
                                  className="font-medium text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                                  data-testid="dashboard-recent-scan-project-link"
                                >
                                  {scan.project_name}
                                </Link>
                              ) : (
                                <span className="font-mono">
                                  {scan.project_id.slice(0, 8)}
                                </span>
                              )}
                            </td>
                            <td className="px-3 text-xs text-muted-foreground">
                              {scan.kind}
                            </td>
                            <td className="px-3">
                              <ScanStatusPill status={scan.status} />
                            </td>
                            <td className="px-3 text-xs text-muted-foreground">
                              {scan.started_at ? (
                                <RelativeTime
                                  value={scan.started_at}
                                  locale={locale}
                                />
                              ) : (
                                t("kpi.no_data")
                              )}
                            </td>
                            <td className="px-3 text-right text-xs text-muted-foreground tabular-nums">
                              {dur == null ? t("kpi.no_data") : `${dur}s`}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </CardContent>
            </Card>

            {/* Recent activity slot — placeholder pane.
             *
             * The plan calls for a notification / audit feed here, but the
             * existing /v1/notifications endpoint is per-user (not portfolio)
             * and the audit feed is super-admin-only — neither matches a
             * dashboard widget contract cleanly. For now we land an empty
             * pane that holds the IA slot so a follow-up PR can wire either
             * source without re-shaping the grid. The empty state copy is
             * already i18n'd so the swap-in is purely the data layer.
             */}
            <Card data-testid="dashboard-recent-activity-card">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">
                  {t("recent_activity.heading")}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <EmptyState
                  data-testid="dashboard-recent-activity-empty"
                  icon={<Activity />}
                  title={t("recent_activity.empty")}
                />
              </CardContent>
            </Card>
          </section>
        </>
      )}

      {/* While the very first projects fetch is in flight AND we haven't
          decided empty vs filled, show a top-of-page progress hint so the
          KPI grid skeletons don't read as an empty dashboard. */}
      {isAnyLoading && !projectsLoaded && !hasNoProjects ? (
        <div
          className="px-6 pb-6"
          data-testid="dashboard-loading-spacer"
          aria-hidden
        />
      ) : null}
    </div>
  );
}

