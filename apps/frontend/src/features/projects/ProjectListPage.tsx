import { useQuery } from "@tanstack/react-query";
import { FolderOpen } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { Virtuoso } from "react-virtuoso";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { AxisPill } from "@/features/projects/components/AxisPill";
import { LicenseDistributionChart } from "@/features/projects/components/LicenseDistributionChart";
import { SeverityDistributionChart } from "@/features/projects/components/SeverityDistributionChart";
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
import { toggleNullable } from "@/lib/searchParamsToggle";
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

// W12 — list-page filters now live in the URL so deep-links + back button
// behave like the Project Detail tabs (filter URL persistence consistency).
// Parsers narrow URL strings to the valid filter unions so a stale or hand-
// edited URL ("?status=garbage") falls back to the page's default instead of
// poisoning the typed setters.
const VALID_PROJECT_STATUS: ProjectStatusFilter[] = [
  "all",
  "queued",
  "running",
  "succeeded",
  "failed",
  "idle",
];
const VALID_PROJECT_SORT: ProjectSortKey[] = ["name", "latest_scan", "risk"];
type SeverityFilterKey = "critical" | "high" | "medium" | "low";
const VALID_SEVERITY_FILTER: SeverityFilterKey[] = [
  "critical",
  "high",
  "medium",
  "low",
];
type LicenseFilterKey = "forbidden" | "conditional" | "allowed" | "unknown";
const VALID_LICENSE_FILTER: LicenseFilterKey[] = [
  "forbidden",
  "conditional",
  "allowed",
  "unknown",
];

function parseStatusParam(v: string | null): ProjectStatusFilter {
  return v && (VALID_PROJECT_STATUS as readonly string[]).includes(v)
    ? (v as ProjectStatusFilter)
    : "all";
}
function parseSortParam(v: string | null): ProjectSortKey {
  return v && (VALID_PROJECT_SORT as readonly string[]).includes(v)
    ? (v as ProjectSortKey)
    : "name";
}
function parseSeverityParam(v: string | null): SeverityFilterKey | null {
  return v && (VALID_SEVERITY_FILTER as readonly string[]).includes(v)
    ? (v as SeverityFilterKey)
    : null;
}
function parseLicenseParam(v: string | null): LicenseFilterKey | null {
  return v && (VALID_LICENSE_FILTER as readonly string[]).includes(v)
    ? (v as LicenseFilterKey)
    : null;
}

export function ProjectListPage() {
  const { t } = useTranslation("projects");
  // v2.1 B5: in the read-only live demo, write actions (trigger scan, create
  // project) are disabled in the UI. The backend middleware is the real guard;
  // this just avoids dead-end clicks that would 403.
  const { demoReadOnly } = useDemoMode();

  // W12 — list-page filters are now URL-derived (single source of truth):
  //   ?status=succeeded&severity=critical&license_category=forbidden&sort=risk&search=foo
  // so deep-links / back button / reload restore the exact list view. Defaults
  // ("all" status, "name" sort, null sev/license, empty search) keep the URL
  // clean by NOT writing the param. The Project Detail tabs already work this
  // way; this PR brings the list page in line.
  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = parseStatusParam(searchParams.get("status"));
  const sort = parseSortParam(searchParams.get("sort"));
  // Distribution-card filters — clicking a Severity / License segment on the
  // header card narrows the list to projects whose WORST bucket in that axis
  // matches. Single-select replace (click again or clear chip to broaden back
  // out). Both feed off ``ProjectPublic.{severity_summary,
  // license_category_summary}`` so the filter is client-side; never-scanned
  // projects (``*_summary == null``) never match either filter.
  const severityFilter = parseSeverityParam(searchParams.get("severity"));
  const licenseFilter = parseLicenseParam(
    searchParams.get("license_category"),
  );
  // Search keeps a local typing buffer; the debounced value is what flows
  // into both the filter AND the URL (so per-keystroke typing doesn't spam
  // history entries). Mirrors the ComponentsTab / VulnerabilitiesTab pattern.
  const [query, setQuery] = useState(() => searchParams.get("search") ?? "");
  const [debouncedQuery, setDebouncedQuery] = useState(query);

  // Setters wrap setSearchParams; default values DELETE the param (URL hygiene
  // + back button restores the exact view). `replace: false` so the back
  // button steps through filter changes (matches Overview chart deep-links).
  const setStatusFilter = useCallback(
    (next: ProjectStatusFilter) => {
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          if (next === "all") out.delete("status");
          else out.set("status", next);
          return out;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );
  const setSort = useCallback(
    (next: ProjectSortKey) => {
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          if (next === "name") out.delete("sort");
          else out.set("sort", next);
          return out;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );
  const toggleSeverityFilter = useCallback(
    (key: SeverityFilterKey) => {
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          const current = parseSeverityParam(out.get("severity"));
          const next = toggleNullable(current, key);
          if (next === null) out.delete("severity");
          else out.set("severity", next);
          return out;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );
  const clearSeverityFilter = useCallback(() => {
    setSearchParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        out.delete("severity");
        return out;
      },
      { replace: false },
    );
  }, [setSearchParams]);
  const toggleLicenseFilter = useCallback(
    (key: LicenseFilterKey) => {
      setSearchParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          const current = parseLicenseParam(out.get("license_category"));
          const next = toggleNullable(current, key);
          if (next === null) out.delete("license_category");
          else out.set("license_category", next);
          return out;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );
  const clearLicenseFilter = useCallback(() => {
    setSearchParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        out.delete("license_category");
        return out;
      },
      { replace: false },
    );
  }, [setSearchParams]);
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

  // Sync the debounced search into the URL so reload / share preserves it.
  // Effect runs after every debounce flush; we skip writing when the param
  // is already up-to-date to avoid redundant history entries.
  useEffect(() => {
    setSearchParams(
      (prev) => {
        const current = prev.get("search") ?? "";
        if (current === debouncedQuery) return prev;
        const out = new URLSearchParams(prev);
        if (debouncedQuery) out.set("search", debouncedQuery);
        else out.delete("search");
        return out;
      },
      { replace: false },
    );
  }, [debouncedQuery, setSearchParams]);

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
      if (severityFilter !== null) {
        const s = project.severity_summary;
        if (!s) return false;
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
        if (worst !== severityFilter) return false;
      }
      if (licenseFilter !== null) {
        const l = project.license_category_summary;
        if (!l) return false;
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
        if (worst !== licenseFilter) return false;
      }
      if (normalized.length === 0) return true;
      return (
        project.name.toLowerCase().includes(normalized) ||
        (project.git_url ?? "").toLowerCase().includes(normalized) ||
        project.slug.toLowerCase().includes(normalized)
      );
    });
    const sorter = SORTERS[sort];
    return [...filtered].sort(sorter);
  }, [items, debouncedQuery, statusFilter, severityFilter, licenseFilter, sort]);

  // By-PROJECT axis distributions — collapse each project's worst bucket and
  // count the projects that land in each. Never-scanned projects are skipped
  // (``*_summary == null``). Empty when no projects loaded yet (the cards
  // hide while the list is loading via the ``items`` gate below).
  const severityDistByProject = useMemo(() => {
    const counts = { critical: 0, high: 0, medium: 0, low: 0 };
    for (const p of items ?? []) {
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
  }, [items]);

  const licenseDistByProject = useMemo(() => {
    const counts = { forbidden: 0, conditional: 0, allowed: 0, unknown: 0 };
    for (const p of items ?? []) {
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
  }, [items]);

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
      {/* W11-B polish — page header sits at the standard 48 px height. The
          title gets the W11-A heading hierarchy (semibold + tight tracking
          inherited from index.css). Background stays canvas (off-white) so
          the cards / table below pop as white surfaces. */}
      <header
        className="flex items-center justify-between border-b bg-background px-6"
        style={{ height: "var(--layout-header)" }}
      >
        <div>
          <h1 className="text-base font-semibold tracking-tight">
            {t("page.title")}
          </h1>
          {/* BUGHUNTER-GOLDEN(explore-a11y-missing-alt): alt 속성 없는 <img> → axe image-alt 위반.
              /login 의 GOLD-P2-002 와 달리 이건 로그인 후 authed 화면(/projects)에 있어,
              L1 explore 가 고정 시나리오 없이 자율 탐색으로 도달해야만 a11y oracle 이 잡는다. */}
          <img src="/vite.svg" width={16} height={16} />
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

      {/* By-PROJECT axis distribution cards above the toolbar. Both cards are
          interactive: segment click narrows the list to projects whose worst
          bucket in that axis matches. Data is derived client-side from
          ``ProjectPublic.{severity_summary, license_category_summary}`` so the
          axis label ("by project") stays honest. */}
      {items && items.length > 0 ? (
        // W11-B polish — distribution band uses the standard 24 px gutter
        // (px-6) so it lines up with the toolbar / rows below. Gap stays at
        // 16 px (gap-4) between the two cards.
        <div
          className="grid items-start gap-4 border-b bg-background px-6 py-4 md:grid-cols-2"
          data-testid="project-list-distribution-cards"
        >
          <Card data-testid="project-list-severity-card">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-baseline gap-2 text-base">
                <span>{t("summary.severity_card.title")}</span>
                <AxisPill>
                  {t("summary.severity_card.axis_projects")}
                </AxisPill>
              </CardTitle>
              <CardDescription>
                {t("summary.severity_card.subtitle")}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <SeverityDistributionChart
                distribution={severityDistByProject}
                onSegmentClick={(key) => {
                  // W9-#57 — re-clicking the active segment toggles the filter
                  // OFF. Only the four ranked buckets are filterable; ``info``
                  // / ``none`` segments stay inert. W12 — toggle now flows
                  // through the URL setter so the back button restores it.
                  if (
                    key === "critical" ||
                    key === "high" ||
                    key === "medium" ||
                    key === "low"
                  ) {
                    toggleSeverityFilter(key);
                  }
                }}
              />
            </CardContent>
          </Card>
          <Card data-testid="project-list-license-card">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-baseline gap-2 text-base">
                <span>{t("summary.license_card.title")}</span>
                <AxisPill>
                  {t("summary.license_card.axis_projects")}
                </AxisPill>
              </CardTitle>
              <CardDescription>
                {t("summary.license_card.subtitle")}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <LicenseDistributionChart
                distribution={licenseDistByProject}
                onSegmentClick={(key) => {
                  // W9-#57 — re-clicking the same category toggles it off.
                  // W12 — URL-routed so the toggle survives reload / share.
                  toggleLicenseFilter(key);
                }}
              />
            </CardContent>
          </Card>
        </div>
      ) : null}

      {severityFilter !== null || licenseFilter !== null ? (
        // W11-B polish — chip pills land on the muted token (no /30 opacity
        // hack), hover transition follows the 150 ms ease-out-soft curve, and
        // the inner pill carries a subtle shadow so it reads as raised.
        <div
          className="flex items-center gap-2 border-b bg-muted px-6 py-2 text-xs"
          data-testid="project-list-active-filters"
        >
          <span className="font-medium text-muted-foreground">
            {t("summary.filter_chip.label")}
          </span>
          {severityFilter !== null ? (
            <button
              type="button"
              onClick={clearSeverityFilter}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2 py-0.5 shadow-sm transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              data-testid="project-list-active-filter-clear-severity"
            >
              <span>
                {t("summary.filter_chip.severity", {
                  severity: severityFilter,
                })}
              </span>
              <span aria-hidden>×</span>
            </button>
          ) : null}
          {licenseFilter !== null ? (
            <button
              type="button"
              onClick={clearLicenseFilter}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2 py-0.5 shadow-sm transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              data-testid="project-list-active-filter-clear-license"
            >
              <span>
                {t("summary.filter_chip.license", { license: licenseFilter })}
              </span>
              <span aria-hidden>×</span>
            </button>
          ) : null}
        </div>
      ) : null}

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
          <EmptyState
            data-testid="project-list-empty"
            className="m-6"
            icon={<FolderOpen />}
            title={t("empty.title")}
            description={t("empty.subtitle")}
            action={
              demoReadOnly ? (
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
              )
            }
          />
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
            <>
              <ScanProgress
                scanId={scanDrawer.scanId}
                release={scanDrawer.release}
                status={scanDrawer.status ?? "queued"}
                onClose={handleCloseDrawer}
                onCancelled={() =>
                  setScanDrawer((s) => ({ ...s, status: "cancelled" }))
                }
                hideInlineLog
              />
              {/*
               * Always-visible link out to the dedicated full-page log view.
               * The inline log panel was pulled from the drawer — this link
               * takes the user to a real route where they can stream the
               * full log AND download it.
               */}
              <div className="mt-2 border-t pt-3">
                <Link
                  to={`/scans/${scanDrawer.scanId}`}
                  className="text-sm text-primary hover:underline focus-visible:underline focus-visible:outline-none"
                  data-testid="scan-drawer-open-full-log"
                >
                  {t("scans:progress.open_full_log")}
                </Link>
              </div>
            </>
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
    // W11-B polish — Vercel deployments-1 row: subtle hover tint via
    // `--accent` (W11-A token), tightened gutter to `px-6` so the row aligns
    // with toolbar / distribution cards, and a 150 ms ease-out-soft hover
    // transition matching button / dropdown motion. Row height (40 px)
    // unchanged — dense identity stays.
    <div
      data-testid="project-row"
      data-project-id={project.id}
      data-row-index={rowIndex}
      className={cn(
        "flex items-center gap-3 border-b bg-card px-6 text-sm transition-colors duration-fast ease-out-soft hover:bg-accent",
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
      <span
        className="hidden w-44 truncate text-xs text-muted-foreground md:inline-block"
        data-testid="project-row-created-by"
        title={project.created_by_user_name ?? ""}
      >
        {project.created_by_user_name ?? "—"}
      </span>
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
