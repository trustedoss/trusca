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

export function projectOverviewKey(projectId: string, scanId?: string) {
  return ["projects", projectId, "overview", scanId ?? "latest"] as const;
}

export function useProjectOverview(
  projectId: string | undefined,
  scanId?: string,
): UseQueryResult<ProjectOverviewResponse> {
  return useQuery({
    queryKey: projectOverviewKey(projectId ?? "", scanId),
    queryFn: () => getProjectOverview(projectId as string, { scanId }),
    enabled: typeof projectId === "string" && projectId.length > 0,
    // While any recent scan is still queued/running, poll so the "recent
    // scans" table flips from 대기 중/진행 중 → 성공/실패 without a manual
    // page reload. The WebSocket drawer streams live progress but does not
    // invalidate this query, so polling is what keeps the overview fresh.
    // Returns false once every scan is terminal to avoid idle polling.
    refetchInterval: (query) => {
      const active = query.state.data?.recent_scans.some(
        (scan) => scan.status === "queued" || scan.status === "running",
      );
      return active ? 4000 : false;
    },
  });
}
