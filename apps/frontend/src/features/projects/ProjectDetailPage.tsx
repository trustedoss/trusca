import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { useProjectOverview } from "@/features/projects/api/useProjectOverview";
import { ComponentsTab } from "@/features/projects/components/ComponentsTab";
import { OverviewTab } from "@/features/projects/components/OverviewTab";
import { RiskGauge } from "@/features/projects/components/RiskGauge";
import { ProblemError } from "@/lib/problem";
import { getProject } from "@/lib/projectsApi";
import { cn } from "@/lib/utils";

/**
 * ProjectDetailPage — Phase 3 PR #10.
 *
 * Detail page rendered at `/projects/:id`. Houses the tab strip
 * (Overview / Components / Vulnerabilities / Licenses) and a
 * breadcrumb-flavored header with the project name + risk badge.
 *
 * Vulnerabilities and Licenses tabs are placeholder until PR #11 / #12
 * land. Disabled triggers keep the future shape visible without leaking a
 * half-built screen.
 *
 * Tab selection is mirrored into `?tab=…` so reload + deep-link survive.
 */

const ALLOWED_TABS = new Set(["overview", "components", "vulnerabilities", "licenses"]);

export function ProjectDetailPage() {
  const { t } = useTranslation("project_detail");
  const { id: projectId } = useParams<{ id: string }>();
  const [searchParams, setSearchParams] = useSearchParams();

  const tabParam = searchParams.get("tab");
  const activeTab =
    tabParam && ALLOWED_TABS.has(tabParam) ? tabParam : "overview";

  const projectQuery = useQuery({
    queryKey: ["projects", projectId, "summary"],
    queryFn: () => getProject(projectId as string),
    enabled: typeof projectId === "string" && projectId.length > 0,
  });

  // Overview is fetched here too so the header risk badge can render
  // alongside the breadcrumb without waiting for the tab to mount.
  const overview = useProjectOverview(projectId);

  if (!projectId) {
    return (
      <div className="p-6" data-testid="project-detail-missing-id">
        <Alert variant="destructive">
          <AlertDescription>{t("page.missing_id")}</AlertDescription>
        </Alert>
      </div>
    );
  }

  function setTab(next: string) {
    setSearchParams(
      (prev) => {
        const merged = new URLSearchParams(prev);
        // When switching tabs, drop ComponentsTab-specific params so we
        // don't carry a stale severity filter into Overview.
        if (next !== "components") {
          merged.delete("drawer");
          merged.delete("search");
          merged.delete("severity");
          merged.delete("license_category");
          merged.delete("sort");
          merged.delete("order");
        }
        if (next === "overview") {
          merged.delete("tab");
        } else {
          merged.set("tab", next);
        }
        return merged;
      },
      { replace: true },
    );
  }

  return (
    <div
      className="flex min-h-screen flex-col bg-background text-foreground"
      data-testid="project-detail-page"
      data-project-id={projectId}
    >
      <ProjectDetailHeader
        projectId={projectId}
        projectName={projectQuery.data?.name ?? null}
        riskScore={overview.data?.risk_score ?? null}
        isProjectLoading={projectQuery.isLoading}
        isProjectError={projectQuery.isError}
        projectError={projectQuery.error}
      />

      <Tabs value={activeTab} onValueChange={setTab}>
        <TabsList data-testid="project-detail-tabs">
          <TabsTrigger
            value="overview"
            data-testid="project-detail-tab-overview"
          >
            {t("tabs.overview")}
          </TabsTrigger>
          <TabsTrigger
            value="components"
            data-testid="project-detail-tab-components"
          >
            {t("tabs.components")}
          </TabsTrigger>
          <TabsTrigger
            value="vulnerabilities"
            disabled
            data-testid="project-detail-tab-vulnerabilities"
          >
            {t("tabs.vulnerabilities")}
          </TabsTrigger>
          <TabsTrigger
            value="licenses"
            disabled
            data-testid="project-detail-tab-licenses"
          >
            {t("tabs.licenses")}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <OverviewTab projectId={projectId} />
        </TabsContent>
        <TabsContent value="components">
          <ComponentsTab projectId={projectId} />
        </TabsContent>
        <TabsContent value="vulnerabilities">
          <EmptyTabPlaceholder
            type="vulnerabilities"
            data-testid="project-detail-empty-vulnerabilities"
          />
        </TabsContent>
        <TabsContent value="licenses">
          <EmptyTabPlaceholder
            type="licenses"
            data-testid="project-detail-empty-licenses"
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

interface ProjectDetailHeaderProps {
  projectId: string;
  projectName: string | null;
  riskScore: number | null;
  isProjectLoading: boolean;
  isProjectError: boolean;
  projectError: unknown;
}

function ProjectDetailHeader({
  projectId,
  projectName,
  riskScore,
  isProjectLoading,
  isProjectError,
  projectError,
}: ProjectDetailHeaderProps) {
  const { t } = useTranslation("project_detail");
  return (
    <header
      className={cn(
        "flex items-center justify-between gap-4 border-b px-6 py-3",
      )}
      data-testid="project-detail-header"
    >
      <div className="flex flex-col gap-1">
        <nav
          className="flex items-center gap-2 text-xs text-muted-foreground"
          aria-label={t("page.breadcrumb_aria")}
        >
          <Link
            to="/projects"
            className="hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            data-testid="project-detail-breadcrumb-projects"
          >
            {t("page.breadcrumb_projects")}
          </Link>
          <span aria-hidden>/</span>
          <span data-testid="project-detail-breadcrumb-current">
            {projectName ?? t("page.loading_name")}
          </span>
        </nav>
        {isProjectLoading ? (
          <Skeleton className="h-6 w-48" />
        ) : isProjectError ? (
          <span
            className="text-base font-semibold text-destructive"
            data-testid="project-detail-load-error"
          >
            {projectError instanceof ProblemError
              ? projectError.title
              : t("page.load_error")}
          </span>
        ) : (
          <h1
            className="text-lg font-semibold tracking-tight"
            data-testid="project-detail-title"
          >
            {projectName}
          </h1>
        )}
        <span
          className="font-mono text-[10px] text-muted-foreground"
          data-testid="project-detail-id"
        >
          {projectId}
        </span>
      </div>
      {riskScore != null ? (
        <div data-testid="project-detail-risk-badge">
          <RiskGauge score={riskScore} />
        </div>
      ) : null}
    </header>
  );
}

interface EmptyTabPlaceholderProps {
  type: "vulnerabilities" | "licenses";
  ["data-testid"]?: string;
}

function EmptyTabPlaceholder({
  type,
  "data-testid": testId,
}: EmptyTabPlaceholderProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      data-testid={testId}
      className="flex flex-col items-center justify-center gap-2 p-12 text-center text-sm text-muted-foreground"
    >
      <span className="font-medium">{t(`tabs_placeholder.${type}.title`)}</span>
      <span>{t(`tabs_placeholder.${type}.subtitle`)}</span>
    </div>
  );
}
