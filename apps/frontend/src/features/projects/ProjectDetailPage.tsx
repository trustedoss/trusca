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
import { LicensesTab } from "@/features/projects/components/LicensesTab";
import { ObligationsTab } from "@/features/projects/components/ObligationsTab";
import { OverviewTab } from "@/features/projects/components/OverviewTab";
import { RiskGauge } from "@/features/projects/components/RiskGauge";
import { SbomTab } from "@/features/projects/components/SbomTab";
import { SettingsTab } from "@/features/projects/components/SettingsTab";
import { SourceTab } from "@/features/projects/components/SourceTab";
import { VulnerabilitiesTab } from "@/features/projects/components/VulnerabilitiesTab";
import { projectErrorMessageKey } from "@/features/projects/lib/projectErrorMessage";
import { getProject } from "@/lib/projectsApi";
import { cn } from "@/lib/utils";

/**
 * ProjectDetailPage — Phase 3 PR #10.
 *
 * Detail page rendered at `/projects/:id`. Houses the tab strip
 * (Overview / Components / Vulnerabilities / Licenses / Obligations) and a
 * breadcrumb-flavored header with the project name + risk badge.
 *
 * Tab selection is mirrored into `?tab=…` so reload + deep-link survive.
 */

const ALLOWED_TABS = new Set([
  "overview",
  "components",
  "vulnerabilities",
  "licenses",
  "obligations",
  "sbom",
  "source",
  "settings",
]);

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
        // When switching tabs, drop tab-scoped filter params so we don't
        // carry a stale severity filter into Overview. Components,
        // Vulnerabilities, Licenses, and Obligations all use `search` /
        // `sort` / `order`, but they have distinct drawer keys (`drawer` /
        // `vuln` / `license` / `obligation`), distinct multi-filter axes,
        // and distinct pagination semantics.
        if (
          next !== "components" &&
          next !== "vulnerabilities" &&
          next !== "licenses" &&
          next !== "obligations"
        ) {
          merged.delete("search");
          merged.delete("sort");
          merged.delete("order");
        }
        if (next !== "components" && next !== "vulnerabilities") {
          merged.delete("severity");
        }
        if (
          next !== "components" &&
          next !== "licenses" &&
          next !== "obligations"
        ) {
          // license_category is shared by Components, Licenses, and the
          // Obligations tab (PR #13) — drop it when leaving all three so
          // the next non-licensing tab doesn't carry a stale bucket.
          merged.delete("license_category");
        }
        if (next !== "components") {
          merged.delete("drawer");
        }
        if (next !== "vulnerabilities") {
          merged.delete("vuln");
          merged.delete("status");
        }
        if (
          next !== "vulnerabilities" &&
          next !== "licenses" &&
          next !== "obligations"
        ) {
          merged.delete("page");
        }
        if (next !== "licenses" && next !== "obligations") {
          // `kind` is used by both the Licenses tab (declared/concluded/
          // detected) and the Obligations tab (open catalog). Keep it
          // across those two so a deep-link with kind set survives the
          // pivot, but drop it when leaving for an unrelated tab.
          merged.delete("kind");
        }
        if (next !== "licenses") {
          merged.delete("license");
        }
        if (next !== "obligations") {
          merged.delete("obligation");
        }
        if (next !== "source") {
          // The Source tab mirrors the open file path into `?path=`. Drop it
          // when leaving so another tab doesn't inherit a stale file selector
          // (Components/Vulnerabilities use distinct drawer keys, not `path`).
          merged.delete("path");
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
            data-testid="project-detail-tab-vulnerabilities"
          >
            {t("tabs.vulnerabilities")}
          </TabsTrigger>
          <TabsTrigger
            value="licenses"
            data-testid="project-detail-tab-licenses"
          >
            {t("tabs.licenses")}
          </TabsTrigger>
          <TabsTrigger
            value="obligations"
            data-testid="project-detail-tab-obligations"
          >
            {t("tabs.obligations")}
          </TabsTrigger>
          <TabsTrigger value="sbom" data-testid="project-detail-tab-sbom">
            {t("tabs.sbom")}
          </TabsTrigger>
          <TabsTrigger
            value="source"
            data-testid="project-detail-tab-source"
          >
            {t("tabs.source")}
          </TabsTrigger>
          <TabsTrigger
            value="settings"
            data-testid="project-detail-tab-settings"
          >
            {t("tabs.settings")}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <OverviewTab projectId={projectId} />
        </TabsContent>
        <TabsContent value="components">
          <ComponentsTab projectId={projectId} />
        </TabsContent>
        <TabsContent value="vulnerabilities">
          <VulnerabilitiesTab
            projectId={projectId}
            projectName={projectQuery.data?.name ?? null}
          />
        </TabsContent>
        <TabsContent value="licenses">
          <LicensesTab projectId={projectId} />
        </TabsContent>
        <TabsContent value="obligations">
          <ObligationsTab
            projectId={projectId}
            projectName={projectQuery.data?.name ?? null}
          />
        </TabsContent>
        <TabsContent value="sbom">
          <SbomTab
            projectId={projectId}
            lastScanAt={overview.data?.last_scan_at ?? null}
          />
        </TabsContent>
        <TabsContent value="source">
          <SourceTab
            projectId={projectId}
            projectName={projectQuery.data?.name ?? null}
          />
        </TabsContent>
        <TabsContent value="settings">
          <SettingsTab
            projectId={projectId}
            project={projectQuery.data ?? null}
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
            {/* BUG-002: localize the RFC 7807 problem (404/403) instead of
                rendering the backend's English `title` (e.g. "Project Not
                Found") so the KO locale shows Korean. */}
            {t(projectErrorMessageKey(projectError, "page.errors"), {
              defaultValue: t("page.load_error"),
            })}
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

