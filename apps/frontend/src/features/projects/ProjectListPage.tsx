import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Virtuoso } from "react-virtuoso";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ProjectStatusBadge } from "@/features/projects/components/ProjectStatusBadge";
import {
  ProjectListToolbar,
  type ProjectSortKey,
  type ProjectStatusFilter,
} from "@/features/projects/components/ProjectListToolbar";
import { ScanProgress } from "@/features/scan/ScanProgress";
import { SourceSelectDialog } from "@/features/scan/SourceSelectDialog";
import { useDemoMode } from "@/hooks/useDemoMode";
import {
  listProjects,
  type ProjectPublic,
  type ProjectSeveritySummary,
  type ScanPublic,
  type ScanStatus,
} from "@/lib/projectsApi";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

/**
 * ProjectListPage — Phase 2 PR #9 task 2.11.
 *
 * Virtualized project list + inline filter toolbar + Sheet-based scan
 * progress drawer. We fetch one page (size=100, the backend `GET
 * /v1/projects` ceiling) and do client-side search/sort/filter — server-side
 * cursor pagination is a follow-up (TODO in handoff). 100 keeps virtualization
 * meaningful and matches the API contract; raising the ceiling is a backend
 * change tracked separately.
 */

const PROJECT_PAGE_SIZE = 100;

interface ScanDrawerState {
  open: boolean;
  scanId: string | null;
  projectName: string | null;
  status: ScanStatus | null;
  release: string | null;
}

interface SourceDialogState {
  open: boolean;
  project: ProjectPublic | null;
}

function compareByName(a: ProjectPublic, b: ProjectPublic): number {
  return a.name.localeCompare(b.name);
}

function compareByLatestScan(a: ProjectPublic, b: ProjectPublic): number {
  // Most recent first. updated_at is a sensible fallback when latest_scan_id
  // is null because the project has never been scanned.
  const aT = a.updated_at;
  const bT = b.updated_at;
  return bT.localeCompare(aT);
}

function compareByRisk(a: ProjectPublic, b: ProjectPublic): number {
  // Risk score is not yet on the project wire shape. We surface a stable
  // alphabetical fallback so the dropdown is not a no-op for users; the
  // dedicated /projects/{id}/risk endpoint lands in PR #11.
  return compareByName(a, b);
}

const SORTERS: Record<
  ProjectSortKey,
  (a: ProjectPublic, b: ProjectPublic) => number
> = {
  name: compareByName,
  latest_scan: compareByLatestScan,
  risk: compareByRisk,
};

function statusFilterMatches(
  project: ProjectPublic,
  filter: ProjectStatusFilter,
): boolean {
  if (filter === "all") return true;
  // `latest_scan_status` is now on the wire shape, so the status filter can
  // narrow for real. "idle" = never scanned (no latest_scan_status). The other
  // buckets compare against the latest scan attempt's status.
  if (filter === "idle") return project.latest_scan_status == null;
  return project.latest_scan_status === filter;
}

export function ProjectListPage() {
  const { t } = useTranslation("projects");
  // v2.1 B5: in the read-only live demo, write actions (trigger scan, create
  // project) are disabled in the UI. The backend middleware is the real guard;
  // this just avoids dead-end clicks that would 403.
  const { demoReadOnly } = useDemoMode();

  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<ProjectStatusFilter>("all");
  const [sort, setSort] = useState<ProjectSortKey>("name");
  const [scanDrawer, setScanDrawer] = useState<ScanDrawerState>({
    open: false,
    scanId: null,
    projectName: null,
    status: null,
    release: null,
  });
  const [sourceDialog, setSourceDialog] = useState<SourceDialogState>({
    open: false,
    project: null,
  });

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setDebouncedQuery(query), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  const projectsQuery = useQuery({
    queryKey: ["projects", { page: 1, size: PROJECT_PAGE_SIZE }],
    queryFn: () => listProjects({ page: 1, size: PROJECT_PAGE_SIZE }),
  });

  const items = projectsQuery.data?.items;

  const filteredItems = useMemo(() => {
    const source = items ?? [];
    const normalized = debouncedQuery.trim().toLowerCase();
    const filtered = source.filter((project) => {
      if (!statusFilterMatches(project, statusFilter)) return false;
      if (normalized.length === 0) return true;
      return (
        project.name.toLowerCase().includes(normalized) ||
        (project.git_url ?? "").toLowerCase().includes(normalized) ||
        project.slug.toLowerCase().includes(normalized)
      );
    });
    const sorter = SORTERS[sort];
    return [...filtered].sort(sorter);
  }, [items, debouncedQuery, statusFilter, sort]);

  function handleScanStarted(scan: ScanPublic, project: ProjectPublic) {
    setScanDrawer({
      open: true,
      scanId: scan.id,
      projectName: project.name,
      status: scan.status,
      release: scan.release,
    });
  }

  function handleCloseDrawer() {
    setScanDrawer((s) => ({ ...s, open: false }));
  }

  function handleOpenSourceDialog(project: ProjectPublic) {
    setSourceDialog({ open: true, project });
  }

  const isLoading = projectsQuery.isLoading;
  const isError = projectsQuery.isError;
  const isEmpty = !isLoading && !isError && filteredItems.length === 0;

  return (
    <div
      className="flex min-h-screen flex-col bg-background text-foreground"
      data-testid="project-list-page"
    >
      <header
        className="flex items-center justify-between border-b px-6"
        style={{ height: "var(--layout-header)" }}
      >
        <div>
          <h1 className="text-sm font-semibold tracking-tight">
            {t("page.title")}
          </h1>
        </div>
        {demoReadOnly ? (
          <Button
            size="sm"
            disabled
            title={t("demo.write_disabled")}
            data-testid="project-list-register"
          >
            {t("page.register")}
          </Button>
        ) : (
          <Button asChild size="sm" data-testid="project-list-register">
            <Link to="/projects/new">{t("page.register")}</Link>
          </Button>
        )}
      </header>

      <div className="flex flex-col">
        <ProjectListToolbar
          query={query}
          onQueryChange={setQuery}
          status={statusFilter}
          onStatusChange={setStatusFilter}
          sort={sort}
          onSortChange={setSort}
        />
      </div>

      <main className="flex flex-1 flex-col" data-testid="project-list-main">
        {isError ? (
          <div className="px-6 py-6">
            <Alert variant="destructive" data-testid="project-list-error">
              <AlertDescription>{t("errors.load_failed")}</AlertDescription>
            </Alert>
          </div>
        ) : null}

        {isLoading ? (
          <div className="flex flex-col gap-2 px-6 py-4" data-testid="project-list-loading">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : null}

        {isEmpty ? (
          <Card className="m-6" data-testid="project-list-empty">
            <CardHeader>
              <CardTitle className="text-base">{t("empty.title")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                {t("empty.subtitle")}
              </p>
              {demoReadOnly ? (
                <Button
                  data-testid="project-list-empty-cta"
                  disabled
                  title={t("demo.write_disabled")}
                >
                  {t("empty.cta")}
                </Button>
              ) : (
                <Button asChild data-testid="project-list-empty-cta">
                  <Link to="/projects/new">{t("empty.cta")}</Link>
                </Button>
              )}
            </CardContent>
          </Card>
        ) : null}

        {!isLoading && !isError && filteredItems.length > 0 ? (
          <div
            className="flex-1"
            data-testid="project-list-virtual"
            data-total={filteredItems.length}
          >
            <Virtuoso
              data={filteredItems}
              style={{ height: "calc(100vh - var(--layout-header) - 56px)" }}
              itemContent={(index, project) => (
                <ProjectRow
                  project={project}
                  onScan={() => handleOpenSourceDialog(project)}
                  rowIndex={index}
                  writeDisabled={demoReadOnly}
                />
              )}
            />
          </div>
        ) : null}
      </main>

      {sourceDialog.project ? (
        <SourceSelectDialog
          open={sourceDialog.open}
          onOpenChange={(open) =>
            setSourceDialog((s) => ({ ...s, open }))
          }
          project={sourceDialog.project}
          onScanStarted={handleScanStarted}
        />
      ) : null}

      <Sheet
        open={scanDrawer.open}
        onOpenChange={(open) =>
          setScanDrawer((s) => ({ ...s, open }))
        }
      >
        <SheetContent
          side="right"
          className="flex flex-col gap-4"
          data-testid="scan-progress-drawer"
        >
          <SheetHeader>
            <SheetTitle>{scanDrawer.projectName ?? ""}</SheetTitle>
            <SheetDescription>{t("page.subtitle")}</SheetDescription>
          </SheetHeader>
          {scanDrawer.scanId ? (
            <ScanProgress
              scanId={scanDrawer.scanId}
              release={scanDrawer.release}
              status={scanDrawer.status ?? "queued"}
              onClose={handleCloseDrawer}
              onCancelled={() =>
                setScanDrawer((s) => ({ ...s, status: "cancelled" }))
              }
            />
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}

interface ProjectRowProps {
  project: ProjectPublic;
  onScan: () => void;
  rowIndex: number;
  writeDisabled?: boolean;
}

function ProjectRow({
  project,
  onScan,
  rowIndex,
  writeDisabled = false,
}: ProjectRowProps) {
  const { t } = useTranslation("projects");
  return (
    <div
      data-testid="project-row"
      data-project-id={project.id}
      data-row-index={rowIndex}
      className={cn(
        "flex items-center gap-3 border-b px-4 text-sm",
      )}
      style={{ height: "var(--table-row)" }}
    >
      <div className="flex flex-1 items-center gap-3 truncate">
        <Link
          to={`/projects/${project.id}`}
          className="truncate font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          title={project.name}
          data-testid="project-row-link"
          data-project-id={project.id}
        >
          {project.name}
        </Link>
        <span
          className="truncate font-mono text-xs text-muted-foreground"
          title={project.git_url ?? ""}
        >
          {project.git_url ?? ""}
        </span>
      </div>
      <SeveritySummary summary={project.severity_summary} />
      <ScanMetadataSummary project={project} />
      <div data-testid="project-row-status">
        <ProjectStatusBadge status={project.latest_scan_status ?? "idle"} />
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={onScan}
        disabled={writeDisabled}
        title={writeDisabled ? t("demo.write_disabled") : undefined}
        data-testid="project-row-scan"
        data-project-name={project.name}
      >
        {t("row.trigger_scan")}
      </Button>
    </div>
  );
}

interface SeverityBucket {
  key: keyof ProjectSeveritySummary;
  count: number;
  /** Tailwind risk token class — never a hex literal (CLAUDE.md design tokens). */
  colorClass: string;
  /** Short uppercase label (C / H / M / L), translated for screen readers. */
  abbrevKey: string;
}

/**
 * Compact per-row vulnerability-severity summary, e.g. `C 10 · H 13 · M 17 ·
 * L 27`. Renders nothing when the project has no succeeded scan
 * (`summary == null`) or every bucket is 0. Colors come from the design risk
 * tokens; each count is paired with a letter label so color is not the only
 * signal (CLAUDE.md accessibility rule).
 */
function SeveritySummary({
  summary,
}: {
  summary: ProjectSeveritySummary | null;
}) {
  const { t } = useTranslation("projects");
  if (summary == null) return null;

  const buckets: SeverityBucket[] = [
    {
      key: "critical",
      count: summary.critical,
      colorClass: "text-risk-critical",
      abbrevKey: "severity.abbrev.critical",
    },
    {
      key: "high",
      count: summary.high,
      colorClass: "text-risk-high",
      abbrevKey: "severity.abbrev.high",
    },
    {
      key: "medium",
      count: summary.medium,
      colorClass: "text-risk-medium",
      abbrevKey: "severity.abbrev.medium",
    },
    {
      key: "low",
      count: summary.low,
      colorClass: "text-risk-low",
      abbrevKey: "severity.abbrev.low",
    },
  ];
  const present = buckets.filter((b) => b.count > 0);
  if (present.length === 0) return null;

  return (
    <div
      className="flex items-center gap-2 font-mono text-xs"
      data-testid="project-row-severity"
      aria-label={t("severity.summary_aria")}
    >
      {present.map((bucket, idx) => (
        <span key={bucket.key} className="flex items-center gap-2">
          {idx > 0 ? (
            <span aria-hidden className="text-muted-foreground">
              ·
            </span>
          ) : null}
          <span className={cn("font-medium", bucket.colorClass)}>
            <span aria-hidden>{t(bucket.abbrevKey)}</span> {bucket.count}
          </span>
        </span>
      ))}
    </div>
  );
}

/**
 * Compact per-row scan-aggregate summary, e.g.
 * `Rel 12 · Scn 47 · 2h ago` (W3 #30).
 *
 * Renders nothing when the project has never been scanned
 * (`last_scan_at == null` && `scan_count === 0`) — matches the
 * SeveritySummary "render nothing when there's nothing to show" pattern so
 * never-scanned rows stay visually clean. A project with attempts but zero
 * releases (e.g. one failed scan) still renders the cluster — there's
 * discoverability value in surfacing "we tried, it failed" via the visible
 * `Scn N` count and timestamp.
 *
 * Colour is `text-muted-foreground` (a design token, never a hex literal):
 * this is a navigation/discoverability cue, NOT a risk signal — risk signals
 * live in the SeveritySummary's risk tokens. The relative timestamp is
 * paired with an absolute-ISO `title` tooltip so power users can hover for
 * the exact moment.
 */
function ScanMetadataSummary({ project }: { project: ProjectPublic }) {
  const { t, i18n } = useTranslation("projects");
  const lastScanAt = project.last_scan_at;
  const scanCount = project.scan_count;
  const releaseCount = project.release_count;

  // Never-scanned: skip the cluster entirely. Defensive on both fields —
  // a future shape where last_scan_at is null but scan_count > 0 would still
  // be worth surfacing ("we tried, lost the timestamp"), and vice versa.
  if (lastScanAt == null && scanCount === 0) return null;

  const locale = i18n.resolvedLanguage;
  const relative =
    lastScanAt != null
      ? formatRelativeToNow(lastScanAt, locale)
      : t("row.never_scanned");

  const ariaLabel = t("row.scan_meta_aria", {
    releases: releaseCount,
    scans: scanCount,
    when: relative,
  });

  return (
    <div
      className="flex items-center gap-2 font-mono text-xs text-muted-foreground"
      data-testid="project-row-scan-meta"
      data-release-count={releaseCount}
      data-scan-count={scanCount}
      aria-label={ariaLabel}
    >
      <span>
        <span aria-hidden>{t("row.releases_abbrev")}</span> {releaseCount}
      </span>
      <span aria-hidden>·</span>
      <span>
        <span aria-hidden>{t("row.scans_abbrev")}</span> {scanCount}
      </span>
      <span aria-hidden>·</span>
      <span title={lastScanAt ?? undefined} data-testid="project-row-scan-meta-when">
        {relative}
      </span>
    </div>
  );
}
