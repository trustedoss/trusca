import { useQuery } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useParams } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { useComponent } from "@/features/projects/api/useComponent";
import { ComponentDetailBody } from "@/features/projects/components/ComponentDetailBody";
import { projectErrorMessageKey } from "@/features/projects/lib/projectErrorMessage";
import { getProject } from "@/lib/projectsApi";
import { ProblemError } from "@/lib/problem";

/**
 * ComponentDetailPage — W10-E.
 *
 * Full-page surface for a single component at
 * ``/projects/:projectId/components/:componentId``. Complements the existing
 * right-side drawer surface (`ComponentDrawer`) — both render the shared
 * `ComponentDetailBody` so the two surfaces stay byte-for-byte in sync.
 *
 * Why a dedicated page?
 *   - Drawer is great for quick checks from the list. For deep work
 *     (reviewing the raw_data JSON, scanning a long vulnerabilities list,
 *     copy-paste into a procurement/approval ticket) a full-width route is
 *     better — drawer caps at ~xl and overlays the list behind it.
 *   - URL is shareable: copy/paste a `/projects/<id>/components/<cid>` link
 *     and the recipient lands on the same component without the drawer
 *     dance.
 *
 * Backward-compat: the legacy deep link
 * `/projects/:id?tab=components&drawer=<id>` continues to open the drawer
 * surface (untouched in this phase). The two surfaces co-exist.
 *
 * Scope cap (W10-E):
 *   - No NEXT STEPS sidebar in this phase. The natural sidebar content for a
 *     component is an Approval workflow toggle (the model exists at
 *     `models/component_approval.py`) plus an upgrade-recommendation summary.
 *     Wiring it in requires a backend `GET /v1/approvals?component_id=&
 *     project_id=` filter that the current router doesn't expose — adding it
 *     touches the approvals service. We defer to a follow-up rather than
 *     introduce backend work mid-frontend phase.
 *
 * The component-detail page intentionally does NOT mount a snapshot-read-only
 * mode (the drawer surface doesn't carry a read-only path for components
 * either — it's a vulnerabilities-specific concern).
 */

export function ComponentDetailPage() {
  const { t } = useTranslation("project_detail");
  const { projectId, componentId } = useParams<{
    projectId: string;
    componentId: string;
  }>();
  const location = useLocation();

  // Header context — the project name surfaces in the breadcrumb. We use the
  // bare `/v1/projects/{id}` summary instead of the overview (cheaper, no
  // recent_scans polling).
  const projectQuery = useQuery({
    queryKey: ["projects", projectId, "summary"],
    queryFn: () => getProject(projectId as string),
    enabled: typeof projectId === "string" && projectId.length > 0,
  });

  // Component detail — same hook the drawer uses, so the TanStack cache key
  // is shared. Opening the drawer on the same component after visiting this
  // page (or vice-versa) hits the warm cache.
  const detail = useComponent(componentId);

  // ---- Render guards --------------------------------------------------------

  if (!projectId || !componentId) {
    // The router types both params; this guard only catches the impossible-by-
    // route-definition case where a hand-edited URL omits one.
    return (
      <div className="p-6" data-testid="component-detail-page-missing-id">
        <Alert variant="destructive">
          <AlertDescription>{t("page.missing_id")}</AlertDescription>
        </Alert>
      </div>
    );
  }

  // W10-E: if we arrived here from the drawer ("Open in full view"), the
  // drawer stashed the originating list URL in `location.state.from`. We
  // prefer that URL so the user lands back on the same filtered/paginated
  // view — but only if it points at the same project. A mismatched `from`
  // (e.g. user hand-edited the URL or navigated cross-project) silently
  // falls back to the default. We don't accept absolute URLs either, only
  // app-internal paths.
  const defaultBackHref = `/projects/${projectId}?tab=components`;
  const fromState =
    location.state &&
    typeof (location.state as { from?: unknown }).from === "string"
      ? (location.state as { from: string }).from
      : null;
  const backToListHref =
    fromState &&
    fromState.startsWith(`/projects/${projectId}`) &&
    !fromState.startsWith("//")
      ? fromState
      : defaultBackHref;

  // Detail label — used in title + breadcrumb trailing position. Component
  // identity is "name@version" rather than the UUID for human-readable copy.
  const detailLabel = detail.data
    ? `${detail.data.name}@${detail.data.version}`
    : null;

  return (
    <div
      className="flex min-h-screen flex-col bg-background text-foreground"
      data-testid="component-detail-page"
      data-project-id={projectId}
      data-component-id={componentId}
    >
      <PageHeader
        projectId={projectId}
        projectName={projectQuery.data?.name ?? null}
        isProjectError={projectQuery.isError}
        detailLabel={detailLabel}
        isDetailLoading={detail.isLoading}
        backToListHref={backToListHref}
      />

      <main
        // Single-column layout for now — no sidebar in this phase (see scope
        // cap in the file docstring). The max-width cap keeps long lines
        // scannable on >1440 px screens.
        className="mx-auto flex w-full max-w-[1440px] flex-col gap-6 px-6 py-4"
      >
        <div
          className="flex min-w-0 flex-1 flex-col gap-4"
          data-testid="component-detail-page-main"
        >
          {detail.isLoading ? (
            <div
              className="flex flex-col gap-3"
              data-testid="component-detail-page-loading"
            >
              <Skeleton className="h-6 w-1/3" />
              <Skeleton className="h-6 w-2/3" />
              <Skeleton className="h-32 w-full" />
            </div>
          ) : null}

          {detail.isError ? (
            <Alert
              variant="destructive"
              data-testid="component-detail-page-error"
            >
              <AlertDescription>
                {detail.error instanceof ProblemError &&
                (detail.error.status === 404 || detail.error.status === 403)
                  ? t("components.detail_page.not_found")
                  : t(
                      projectErrorMessageKey(
                        detail.error,
                        "components.detail_page.errors",
                      ),
                      {
                        defaultValue: t("components.detail_page.not_found"),
                      },
                    )}
              </AlertDescription>
            </Alert>
          ) : null}

          {detail.data ? <ComponentDetailBody detail={detail.data} /> : null}
        </div>
      </main>
    </div>
  );
}

interface PageHeaderProps {
  projectId: string;
  projectName: string | null;
  isProjectError: boolean;
  detailLabel: string | null;
  isDetailLoading: boolean;
  backToListHref: string;
}

/**
 * Page header — breadcrumb (Projects / project / Components / name@version) +
 * a leading "Back to Components" affordance. The breadcrumb crumbs are plain
 * `Link`s (no shadcn breadcrumb primitive in the codebase yet) so the styling
 * matches the existing `VulnerabilityDetailPage` header.
 */
function PageHeader({
  projectId,
  projectName,
  isProjectError,
  detailLabel,
  isDetailLoading,
  backToListHref,
}: PageHeaderProps) {
  const { t } = useTranslation("project_detail");
  const projectHref = `/projects/${projectId}`;

  const projectCrumb =
    projectName ??
    (isProjectError
      ? t("page.breadcrumb_unavailable")
      : t("page.loading_name"));
  const detailCrumb =
    detailLabel ?? (isDetailLoading ? t("page.loading_name") : "");

  return (
    <header
      className="flex flex-col gap-2 border-b px-6 py-3"
      data-testid="component-detail-page-header"
    >
      <nav
        className="flex items-center gap-2 text-xs text-muted-foreground"
        aria-label={t("components.detail_page.breadcrumb_aria", {
          defaultValue: t("page.breadcrumb_aria"),
        })}
      >
        <Link
          to="/projects"
          className="transition-colors duration-fast ease-out-soft hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="component-detail-page-breadcrumb-projects"
        >
          {t("components.detail_page.breadcrumb.projects")}
        </Link>
        <span aria-hidden>/</span>
        <Link
          to={projectHref}
          className="transition-colors duration-fast ease-out-soft hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="component-detail-page-breadcrumb-project"
        >
          {projectCrumb}
        </Link>
        <span aria-hidden>/</span>
        <Link
          to={backToListHref}
          className="transition-colors duration-fast ease-out-soft hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="component-detail-page-breadcrumb-components"
        >
          {t("components.detail_page.breadcrumb.components")}
        </Link>
        <span aria-hidden>/</span>
        <span
          className="font-mono"
          data-testid="component-detail-page-breadcrumb-current"
        >
          {detailCrumb}
        </span>
      </nav>
      <div className="flex items-center gap-3">
        <Link
          to={backToListHref}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors duration-fast ease-out-soft hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="component-detail-page-back-link"
        >
          <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
          {t("components.detail_page.back_link")}
        </Link>
      </div>
      <h1
        className="text-lg font-semibold tracking-tight"
        data-testid="component-detail-page-title"
      >
        {isDetailLoading
          ? t("components.detail_page.title_loading")
          : (detailLabel ?? "")}
      </h1>
    </header>
  );
}
