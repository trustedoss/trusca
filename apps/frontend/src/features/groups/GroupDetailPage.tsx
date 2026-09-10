// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useQuery } from "@tanstack/react-query";
import {
  ChevronRight,
  ClipboardCheck,
  FolderTree,
  FolderX,
  Plus,
  ScanLine,
  UserPlus,
} from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useGroupDetail } from "@/features/groups/api/useGroupDetail";
import { useGroups } from "@/features/groups/api/useGroups";
import { GroupMembersPanel } from "@/features/groups/components/GroupMembersPanel";
import { ProjectRow } from "@/features/projects/ProjectListPage";
import { useDemoMode } from "@/hooks/useDemoMode";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { useUrlEnum, useUrlFlag } from "@/hooks/useUrlState";
import { listProjects, type ProjectPublic } from "@/lib/projectsApi";
import { useUIStore } from "@/stores/uiStore";

const DETAIL_TABS = ["subgroups", "projects", "members"] as const;
type DetailTab = (typeof DETAIL_TABS)[number];

const PROJECTS_PAGE_SIZE = 100;

/**
 * GroupDetailPage, group-hierarchy Phase 4 PR 4-B.
 *
 * `GET /v1/groups/{id}` 404s uniformly for "does not exist" and "exists,
 * not accessible" (existence-hide, see `GroupsHarness.expectNotFound`'s own
 * docstring). The two render branches below are mutually exclusive on
 * purpose: a 404 renders NO `group-detail-page` node at all, not a page
 * frame wrapped around an error message (the frame itself would be a signal
 * the backend goes out of its way not to give).
 *
 * Policy and Activity sections are deliberately absent: PR 4-A did not ship
 * a group-scoped effective-policy summary or a group-scoped audit log read,
 * so a tab for either here would be a real-looking, permanently-empty
 * surface. Follow-up phase.
 */
export function GroupDetailPage() {
  const { t } = useTranslation("groups");
  const navigate = useNavigate();
  const { groupId } = useParams<{ groupId: string }>();

  const detailQuery = useGroupDetail(groupId);
  const isNotFound =
    detailQuery.isError &&
    (detailQuery.error as { status?: number } | undefined)?.status === 404;
  const isOtherError = detailQuery.isError && !isNotFound;
  const group = detailQuery.data;
  const isLoading = detailQuery.isLoading;

  useDocumentTitle(group?.name, t("page.title"));

  const [tab, setTab] = useUrlEnum<DetailTab>("dtab", DETAIL_TABS, "subgroups");

  const { demoReadOnly } = useDemoMode();
  const setActiveTeamId = useUIStore((s) => s.setActiveTeamId);

  function handleNewProject() {
    if (!group) return;
    // ProjectCreatePage resolves its team exclusively through
    // `useActiveTeam()` (which itself reads this store) and overwrites any
    // URL prefill on mount, so a `?team=` query param does not survive;
    // this is the one channel that does. The global bar's team switcher
    // reads the same store, so it visibly follows along.
    setActiveTeamId(group.id);
    navigate("/projects/new");
  }

  if (isNotFound) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center px-6 py-10">
        <EmptyState
          data-testid="group-detail-not-found"
          icon={<FolderX />}
          title={t("detail.not_found.title")}
          description={t("detail.not_found.description")}
          action={
            <Button asChild variant="outline" size="sm">
              <Link to="/groups">{t("detail.not_found.back")}</Link>
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div
      className="flex min-h-screen flex-col bg-background text-foreground"
      data-testid="group-detail-page"
      data-group-id={groupId}
    >
      {isLoading ? (
        <div className="px-6 py-6" data-testid="group-detail-loading">
          <Skeleton className="mb-3 h-4 w-48" />
          <Skeleton className="mb-2 h-8 w-64" />
          <Skeleton className="h-4 w-96" />
        </div>
      ) : isOtherError ? (
        <div className="px-6 py-6">
          <Alert variant="destructive" data-testid="group-detail-error">
            <AlertDescription>{t("detail.errors.load_failed")}</AlertDescription>
          </Alert>
          <Button asChild variant="outline" size="sm" className="mt-3">
            <Link to="/groups">{t("detail.errors.back")}</Link>
          </Button>
        </div>
      ) : group ? (
        <>
          <header className="border-b bg-background px-6 py-4">
            {/* Root-first ancestor chain, NOT including this group itself
                (GroupDetail.ancestors). Every segment is clickable, even one
                the caller can no longer reach on its own (see the service's
                own note on why a position indicator does not weaken
                existence-hide): the link may itself 404, which is expected. */}
            <nav
              className="mb-2 flex flex-wrap items-center gap-1 text-xs text-muted-foreground"
              aria-label={t("detail.breadcrumb_nav_aria")}
            >
              <Link
                to="/groups"
                className="rounded px-1 py-0.5 hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                {t("detail.breadcrumb_root_label")}
              </Link>
              {group.ancestors.map((ancestor) => (
                <span key={ancestor.id} className="flex items-center gap-1">
                  <ChevronRight className="h-3 w-3" aria-hidden />
                  <Link
                    to={`/groups/${ancestor.id}`}
                    data-testid="group-detail-breadcrumb-segment"
                    data-group-name={ancestor.name}
                    className="rounded px-1 py-0.5 hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  >
                    {ancestor.name}
                  </Link>
                </span>
              ))}
            </nav>

            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0">
                <h1
                  className="truncate text-xl font-semibold tracking-tight"
                  data-testid="group-detail-name"
                >
                  {group.name}
                </h1>
                {group.description ? (
                  <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
                    {group.description}
                  </p>
                ) : null}
              </div>
              <Button
                size="sm"
                onClick={handleNewProject}
                disabled={demoReadOnly}
                title={demoReadOnly ? t("common:demo.write_disabled") : undefined}
                data-testid="group-detail-new-project"
              >
                <Plus className="h-4 w-4" aria-hidden />
                {t("detail.new_project")}
              </Button>
            </div>

            <GroupStatsPanel windowDays={group.stats.window_days}>
              <StatTile
                testId="group-detail-stat-subtree-scan-count"
                icon={<ScanLine className="h-4 w-4" aria-hidden />}
                label={t("detail.stats.scans")}
                value={group.stats.subtree_scan_count}
              />
              <StatTile
                testId="group-detail-stat-subtree-approvals-processed"
                icon={<ClipboardCheck className="h-4 w-4" aria-hidden />}
                label={t("detail.stats.approvals")}
                value={group.stats.subtree_approvals_processed_count}
              />
              <StatTile
                testId="group-detail-stat-subtree-new-members"
                icon={<UserPlus className="h-4 w-4" aria-hidden />}
                label={t("detail.stats.new_members")}
                value={group.stats.subtree_new_member_count}
              />
            </GroupStatsPanel>
          </header>

          <Tabs value={tab} onValueChange={(v) => setTab(v as DetailTab)}>
            <TabsList data-testid="group-detail-tabs">
              <TabsTrigger value="subgroups" data-testid="group-detail-tab-subgroups">
                {t("detail.tabs.subgroups")}
              </TabsTrigger>
              <TabsTrigger value="projects" data-testid="group-detail-tab-projects">
                {t("detail.tabs.projects")}
              </TabsTrigger>
              <TabsTrigger value="members" data-testid="group-detail-tab-members">
                {t("detail.tabs.members")}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="subgroups">
              <GroupSubgroupsPanel parentId={group.id} />
            </TabsContent>
            <TabsContent value="projects">
              <GroupProjectsPanel groupId={group.id} />
            </TabsContent>
            <TabsContent value="members">
              <GroupMembersPanel groupId={group.id} active={tab === "members"} />
            </TabsContent>
          </Tabs>
        </>
      ) : null}
    </div>
  );
}

function GroupStatsPanel({
  windowDays,
  children,
}: {
  windowDays: number;
  children: ReactNode;
}) {
  const { t } = useTranslation("groups");
  return (
    <div
      className="mt-4 grid gap-3 sm:grid-cols-3"
      data-testid="group-detail-stats-panel"
      data-window-days={windowDays}
    >
      <p className="col-span-full -mb-1 text-xs text-muted-foreground">
        {t("detail.stats.heading", { days: windowDays })}
      </p>
      {children}
    </div>
  );
}

function StatTile({
  testId,
  icon,
  label,
  value,
}: {
  testId: string;
  icon: ReactNode;
  label: string;
  value: number;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 py-3">
        <span className="text-muted-foreground">{icon}</span>
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground">{label}</p>
          <p
            className="text-lg font-semibold tabular-nums"
            data-testid={testId}
            data-value={value}
          >
            {value}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function GroupSubgroupsPanel({ parentId }: { parentId: string }) {
  const { t } = useTranslation("groups");
  const subgroupsQuery = useGroups({ parent_id: parentId, page: 1, page_size: 100 });
  const items = subgroupsQuery.data?.items ?? [];

  return (
    <div className="px-6 py-4" data-testid="group-detail-subgroups-panel">
      {subgroupsQuery.isError ? (
        <Alert variant="destructive">
          <AlertDescription>{t("detail.subgroups.errors.load_failed")}</AlertDescription>
        </Alert>
      ) : subgroupsQuery.isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          data-testid="group-detail-subgroups-empty"
          icon={<FolderTree />}
          title={t("detail.subgroups.empty")}
        />
      ) : (
        <ul className="divide-y rounded-md border" data-testid="group-detail-subgroup-list">
          {items.map((sub) => (
            <li key={sub.id}>
              <Link
                to={`/groups/${sub.id}`}
                data-testid="group-detail-subgroup-row"
                data-group-id={sub.id}
                data-group-name={sub.name}
                className="flex items-center justify-between gap-3 px-3 py-2 text-sm transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset focus-visible:ring-ring"
              >
                <div className="min-w-0 truncate">
                  <span className="font-medium">{sub.name}</span>
                  <span className="ml-2 font-mono text-xs text-muted-foreground">
                    {sub.slug}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <Badge variant="secondary" data-value={sub.child_group_count}>
                    {t("list.row.subgroups", { count: sub.child_group_count })}
                  </Badge>
                  <Badge variant="secondary" data-value={sub.project_count}>
                    {t("list.row.projects", { count: sub.project_count })}
                  </Badge>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function GroupProjectsPanel({ groupId }: { groupId: string }) {
  const { t } = useTranslation("groups");
  const navigate = useNavigate();
  const [includeArchived, setIncludeArchived] = useUrlFlag("archived");

  const projectsQuery = useQuery({
    queryKey: ["projects", { team_id: groupId, include_archived: includeArchived }],
    queryFn: () =>
      listProjects({
        team_id: groupId,
        include_archived: includeArchived,
        size: PROJECTS_PAGE_SIZE,
      }),
  });
  const items = projectsQuery.data?.items ?? [];
  const total = projectsQuery.data?.total ?? 0;

  return (
    <div className="flex flex-col" data-testid="group-detail-projects-panel">
      <div className="flex items-center justify-between border-b bg-muted/20 px-6 py-2">
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(e) => setIncludeArchived(e.target.checked)}
            data-testid="group-detail-projects-include-archived"
          />
          {t("detail.projects.include_archived")}
        </label>
      </div>

      {projectsQuery.isError ? (
        <div className="px-6 py-4">
          <Alert variant="destructive">
            <AlertDescription>{t("detail.projects.errors.load_failed")}</AlertDescription>
          </Alert>
        </div>
      ) : projectsQuery.isLoading ? (
        <div className="flex flex-col gap-2 px-6 py-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          data-testid="group-detail-projects-empty"
          icon={<FolderTree />}
          title={t("detail.projects.empty")}
        />
      ) : (
        <>
          {total > items.length ? (
            <div className="px-6 pt-3">
              <Alert>
                <AlertDescription>
                  {t("projects:list.truncated", {
                    shown: items.length,
                    total,
                  })}
                </AlertDescription>
              </Alert>
            </div>
          ) : null}
          <div data-testid="group-detail-project-list">
            {items.map((project: ProjectPublic, index: number) => (
              <ProjectRow
                key={project.id}
                project={project}
                rowIndex={index}
                showTeamBreadcrumb={false}
                onScan={() => navigate(`/projects/${project.id}`)}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
