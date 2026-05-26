/**
 * DashboardPage — org/team risk portfolio (CLAUDE.md screen spec "/").
 *
 * Compact, information-dense, risk-first overview that replaces the old
 * `/` → `/projects` redirect. Layout (top → bottom):
 *   1. Severity summary row — Critical/High/Medium/Low/Info vulnerability
 *      counts, each pairing the design-system risk color with a dot AND a
 *      label so color is never the only signal (accessibility rule).
 *   2. Portfolio row — project count + scan-status counts
 *      (queued/running/succeeded/failed) + pending-approvals card linking to
 *      /approvals.
 *   3. License category distribution — a small proportional bar
 *      (prohibited/conditional/permissive/unknown).
 *   4. Recent scans — last 10, each row linking to /projects/{project_id}.
 *
 * State handling per CLAUDE.md:
 *   - loading  → skeletons (no spinners).
 *   - error    → destructive <Alert> with an i18n message.
 *   - empty    → friendly empty state pointing at "Register project".
 *
 * Colors come exclusively from the design tokens (var(--risk-*) / Tailwind
 * `text-risk-*` utilities). No hex literals live in this component.
 */
import { ClipboardCheck, FolderPlus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useDashboardSummary,
} from "@/features/dashboard/api/useDashboardSummary";
import type {
  DashboardLicenseCategory,
  DashboardScanStatus,
  DashboardSeverity,
  RecentScan,
} from "@/features/dashboard/api/dashboardApi";
import { ProjectStatusBadge } from "@/features/projects/components/ProjectStatusBadge";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

// Severity buckets in most→least severe display order, each mapped to its
// design-system token utility (defined in tailwind.config / index.css).
const SEVERITY_ORDER: { key: DashboardSeverity; dot: string; text: string }[] = [
  { key: "critical", dot: "bg-risk-critical", text: "text-risk-critical" },
  { key: "high", dot: "bg-risk-high", text: "text-risk-high" },
  { key: "medium", dot: "bg-risk-medium", text: "text-risk-medium" },
  { key: "low", dot: "bg-risk-low", text: "text-risk-low" },
  { key: "info", dot: "bg-risk-info", text: "text-risk-info" },
];

const SCAN_STATUS_ORDER: DashboardScanStatus[] = [
  "queued",
  "running",
  "succeeded",
  "failed",
];

// License categories paired with a risk-tone bar segment. Permissive is the
// "good" outcome so it borrows the emerald success color used elsewhere
// (LicenseCategoryBadge); the rest map onto risk tokens.
const LICENSE_ORDER: {
  key: DashboardLicenseCategory;
  bar: string;
  dot: string;
}[] = [
  { key: "prohibited", bar: "bg-risk-critical", dot: "bg-risk-critical" },
  { key: "conditional", bar: "bg-risk-medium", dot: "bg-risk-medium" },
  { key: "permissive", bar: "bg-emerald-500", dot: "bg-emerald-500" },
  { key: "unknown", bar: "bg-risk-info", dot: "bg-risk-info" },
];

function SeverityCard({
  severityKey,
  count,
  dot,
  text,
}: {
  severityKey: DashboardSeverity;
  count: number;
  dot: string;
  text: string;
}) {
  const { t } = useTranslation("dashboard");
  // P2 #4 — Severity tiles deep-link to the projects list. There is no
  // cross-project vuln aggregation view yet, so we route to `/projects`
  // where the user can scan the per-project severity columns. The card is
  // always clickable (uniform affordance); when `count === 0` the link is
  // still useful — "no Critical CVEs anywhere — confirm in the list".
  return (
    <Link
      to="/projects"
      className="rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      data-testid="dashboard-severity-card-link"
      data-severity={severityKey}
      aria-label={t("severity.card_aria", {
        defaultValue: "{{label}}: {{count}} — view projects",
        label: t(`severity.${severityKey}`),
        count,
      })}
    >
      <Card
        className="p-3 transition-colors hover:bg-accent"
        data-testid="dashboard-severity-card"
        data-severity={severityKey}
        data-count={count}
      >
        <div className="flex items-center gap-2">
          <span
            aria-hidden
            className={cn("inline-block h-2 w-2 rounded-full", dot)}
          />
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {t(`severity.${severityKey}`)}
          </span>
        </div>
        <div
          className={cn("mt-1 text-2xl font-semibold tabular-nums", text)}
          data-testid="dashboard-severity-count"
        >
          {count}
        </div>
      </Card>
    </Link>
  );
}

function StatCard({
  label,
  value,
  testid,
  emphasis = false,
  to,
}: {
  label: string;
  value: number;
  testid: string;
  emphasis?: boolean;
  /**
   * P2 #4 — when set, the card becomes a Link to the matching deep-link
   * destination (e.g. `/projects` for "Projects", `/scans?status=running`
   * for the Running scan-status counter). Omit to render a non-clickable
   * stat card unchanged.
   */
  to?: string;
}) {
  const body = (
    <Card
      className={cn(
        "h-full p-3",
        to ? "transition-colors hover:bg-accent" : undefined,
      )}
      data-testid={testid}
      data-count={value}
    >
      <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div
        className={cn(
          "mt-1 text-2xl font-semibold tabular-nums",
          emphasis && value > 0 ? "text-risk-high" : undefined,
        )}
        data-testid={`${testid}-value`}
      >
        {value}
      </div>
    </Card>
  );
  if (!to) return body;
  return (
    <Link
      to={to}
      className="rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      data-testid={`${testid}-link`}
      aria-label={`${label}: ${value}`}
    >
      {body}
    </Link>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
      {children}
    </h2>
  );
}

function LicenseDistribution({
  counts,
}: {
  counts: Record<DashboardLicenseCategory, number>;
}) {
  const { t } = useTranslation("dashboard");
  const total = LICENSE_ORDER.reduce((sum, l) => sum + (counts[l.key] || 0), 0);

  return (
    <Card className="p-4" data-testid="dashboard-license-card" data-total={total}>
      <SectionTitle>{t("license.title")}</SectionTitle>
      {total === 0 ? (
        <p
          className="text-sm text-muted-foreground"
          data-testid="dashboard-license-empty"
        >
          {t("license.empty")}
        </p>
      ) : (
        <>
          <div
            className="flex h-2 w-full overflow-hidden rounded-full bg-muted"
            data-testid="dashboard-license-bar"
            role="img"
            aria-label={t("license.title")}
          >
            {LICENSE_ORDER.map((l) => {
              const count = counts[l.key] || 0;
              if (count === 0) return null;
              return (
                <div
                  key={l.key}
                  className={cn("h-full", l.bar)}
                  style={{ width: `${(count / total) * 100}%` }}
                  data-testid="dashboard-license-segment"
                  data-category={l.key}
                />
              );
            })}
          </div>
          <ul className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {LICENSE_ORDER.map((l) => (
              <li
                key={l.key}
                className="flex items-center gap-2 text-sm"
                data-testid="dashboard-license-legend"
                data-category={l.key}
              >
                <span
                  aria-hidden
                  className={cn("inline-block h-2 w-2 rounded-full", l.dot)}
                />
                <span className="text-muted-foreground">
                  {t(`license.category.${l.key}`)}
                </span>
                <span className="ml-auto font-semibold tabular-nums">
                  {counts[l.key] || 0}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

function RecentScanRow({
  scan,
  locale,
}: {
  scan: RecentScan;
  locale?: string;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <Link
      to={`/projects/${scan.project_id}`}
      className={cn(
        "flex items-center gap-3 border-b px-4 text-sm last:border-b-0",
        "hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
      )}
      style={{ height: "var(--table-row)" }}
      data-testid="dashboard-recent-scan-row"
      data-scan-id={scan.scan_id}
      data-project-id={scan.project_id}
    >
      <span className="flex-1 truncate font-medium" title={scan.project_name}>
        {scan.project_name}
      </span>
      {scan.release ? (
        <span
          className="inline-flex shrink-0 items-center rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px] font-medium text-foreground"
          data-testid="dashboard-recent-scan-release"
          data-release={scan.release}
          title={t("recent_scans.release_aria", { release: scan.release })}
        >
          {scan.release}
        </span>
      ) : null}
      <Badge
        variant="secondary"
        className="shrink-0 text-xs"
        data-testid="dashboard-recent-scan-kind"
        data-kind={scan.kind}
      >
        {t(`scan_kind.${scan.kind}`)}
      </Badge>
      <ProjectStatusBadge status={scan.status} />
      <span className="w-28 shrink-0 text-right text-xs text-muted-foreground tabular-nums">
        {formatRelativeToNow(scan.finished_at, locale)}
      </span>
    </Link>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6" data-testid="dashboard-loading">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Card key={`sev-${i}`} className="p-3">
            <Skeleton className="mb-2 h-3 w-16" />
            <Skeleton className="h-7 w-10" />
          </Card>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <Card key={`stat-${i}`} className="p-3">
            <Skeleton className="mb-2 h-3 w-16" />
            <Skeleton className="h-7 w-10" />
          </Card>
        ))}
      </div>
      <Card className="p-4">
        <Skeleton className="mb-3 h-3 w-32" />
        <Skeleton className="h-2 w-full" />
      </Card>
      <Card>
        <CardHeader>
          <Skeleton className="h-4 w-40" />
        </CardHeader>
        <CardContent className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={`row-${i}`} className="h-9 w-full" />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

export function DashboardPage() {
  const { t, i18n } = useTranslation("dashboard");
  const summaryQuery = useDashboardSummary();

  const data = summaryQuery.data;
  const isLoading = summaryQuery.isLoading;
  const isError = summaryQuery.isError;
  const isEmpty = !isLoading && !isError && (data?.project_count ?? 0) === 0;

  return (
    <div className="flex h-full flex-col" data-testid="dashboard-page">
      <header
        className="flex shrink-0 items-center border-b bg-card px-6"
        style={{ height: "var(--layout-header)" }}
      >
        <h1 className="text-sm font-semibold tracking-tight">
          {t("title")}
        </h1>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {isError ? (
          <Alert variant="destructive" data-testid="dashboard-error">
            <AlertDescription>{t("errors.load_failed")}</AlertDescription>
          </Alert>
        ) : null}

        {isLoading ? <DashboardSkeleton /> : null}

        {isEmpty ? (
          <Card className="mx-auto max-w-lg" data-testid="dashboard-empty">
            <CardHeader>
              <CardTitle className="text-base">{t("empty.title")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                {t("empty.subtitle")}
              </p>
              <Button asChild data-testid="dashboard-empty-cta">
                <Link to="/projects/new">
                  <FolderPlus className="h-4 w-4" aria-hidden />
                  <span>{t("empty.cta")}</span>
                </Link>
              </Button>
            </CardContent>
          </Card>
        ) : null}

        {!isLoading && !isError && data && !isEmpty ? (
          <div className="space-y-6">
            {/* 1. Severity summary row */}
            <section data-testid="dashboard-severity-section">
              <SectionTitle>{t("severity.title")}</SectionTitle>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                {SEVERITY_ORDER.map((s) => (
                  <SeverityCard
                    key={s.key}
                    severityKey={s.key}
                    count={data.vulnerability_severity_counts[s.key]}
                    dot={s.dot}
                    text={s.text}
                  />
                ))}
              </div>
            </section>

            {/* 2. Portfolio row: projects + scan status + approvals */}
            <section data-testid="dashboard-portfolio-section">
              <SectionTitle>{t("portfolio.title")}</SectionTitle>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                <StatCard
                  label={t("portfolio.projects")}
                  value={data.project_count}
                  testid="dashboard-project-count"
                  to="/projects"
                />
                {SCAN_STATUS_ORDER.map((status) => (
                  <StatCard
                    key={status}
                    label={t(`scan_status.${status}`)}
                    value={data.scan_status_counts[status]}
                    testid={`dashboard-scan-status-${status}`}
                    emphasis={status === "failed"}
                    to={`/scans?status=${status}`}
                  />
                ))}
                <Link
                  to="/approvals"
                  className="rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  data-testid="dashboard-approvals-card"
                  data-count={data.pending_approvals_count}
                >
                  <Card className="h-full p-3 transition-colors hover:bg-accent">
                    <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      <ClipboardCheck className="h-3.5 w-3.5" aria-hidden />
                      {t("portfolio.pending_approvals")}
                    </div>
                    <div
                      className={cn(
                        "mt-1 text-2xl font-semibold tabular-nums",
                        data.pending_approvals_count > 0
                          ? "text-risk-medium"
                          : undefined,
                      )}
                    >
                      {data.pending_approvals_count}
                    </div>
                  </Card>
                </Link>
              </div>
            </section>

            {/* 3. License category distribution */}
            <section data-testid="dashboard-license-section">
              <LicenseDistribution counts={data.license_category_counts} />
            </section>

            {/* 4. Recent scans */}
            <section data-testid="dashboard-recent-section">
              <Card>
                <CardHeader className="border-b py-3">
                  <CardTitle className="text-sm">
                    {t("recent_scans.title")}
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                  {data.recent_scans.length === 0 ? (
                    <p
                      className="px-4 py-6 text-sm text-muted-foreground"
                      data-testid="dashboard-recent-empty"
                    >
                      {t("recent_scans.empty")}
                    </p>
                  ) : (
                    <div data-testid="dashboard-recent-list">
                      {data.recent_scans.slice(0, 10).map((scan) => (
                        <RecentScanRow
                          key={scan.scan_id}
                          scan={scan}
                          locale={i18n.resolvedLanguage}
                        />
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}
