/**
 * useProjectOverview — Phase 3 PR #10.
 *
 * TanStack Query hook for the Overview tab of the project detail page.
 * Query key is `["projects", projectId, "overview"]` so the parent can
 * invalidate it via the `["projects", projectId]` prefix without affecting
 * the components list query.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getProjectOverview,
  type ProjectOverviewResponse,
} from "@/features/projects/api/projectDetailApi";

export function projectOverviewKey(projectId: string) {
  return ["projects", projectId, "overview"] as const;
}

export function useProjectOverview(
  projectId: string | undefined,
): UseQueryResult<ProjectOverviewResponse> {
  return useQuery({
    queryKey: projectOverviewKey(projectId ?? ""),
    queryFn: () => getProjectOverview(projectId as string),
    enabled: typeof projectId === "string" && projectId.length > 0,
  });
}
