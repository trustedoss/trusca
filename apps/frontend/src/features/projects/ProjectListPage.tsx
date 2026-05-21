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
import {
  listProjects,
  type ProjectPublic,
  type ScanPublic,
} from "@/lib/projectsApi";
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
  if (filter === "idle") return project.latest_scan_id == null;
  // Without joined scan rows on the project wire shape (PR #10 backlog) we
  // cannot reliably narrow by running/queued/succeeded/failed yet. Keep the
  // selector visible and permissive so e2e and design checks still flow; a
  // follow-up backend change carries `latest_scan_status` on the row.
  return true;
}

export function ProjectListPage() {
  const { t } = useTranslation("projects");

  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<ProjectStatusFilter>("all");
  const [sort, setSort] = useState<ProjectSortKey>("name");
  const [scanDrawer, setScanDrawer] = useState<ScanDrawerState>({
    open: false,
    scanId: null,
    projectName: null,
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
              <Button data-testid="project-list-empty-cta">
                {t("empty.cta")}
              </Button>
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
              onClose={handleCloseDrawer}
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
}

function ProjectRow({ project, onScan, rowIndex }: ProjectRowProps) {
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
      <ProjectStatusBadge
        status={project.latest_scan_id == null ? "idle" : null}
      />
      <Button
        variant="outline"
        size="sm"
        onClick={onScan}
        data-testid="project-row-scan"
        data-project-name={project.name}
      >
        {t("row.trigger_scan")}
      </Button>
    </div>
  );
}
