/**
 * useProjectDiff — feature #28 Phase 2 (release compare view).
 *
 * Fetches the diff between two succeeded scans of a project. The two scan ids
 * are part of the query key so flipping base/target (or picking a different
 * release in either selector) refetches and caches independently.
 *
 * Query key is `["projects", projectId, "diff", { base, target }]` so it shares
 * the `["projects", projectId]` invalidation prefix with the rest of the project
 * detail surface — a fresh scan that invalidates the project prefix also drops
 * any cached diff (the snapshots themselves are immutable, but the set of
 * available releases changes).
 *
 * The query is disabled until BOTH scan ids are present so a half-resolved URL
 * (`?base=` set, `?target=` not yet) never fires a request that would 422/404.
 */
import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";

import { getProjectDiff, type ProjectDiff } from "@/features/projects/api/diffApi";

export function projectDiffKey(
  projectId: string,
  base: string,
  target: string,
) {
  return ["projects", projectId, "diff", { base, target }] as const;
}

export function useProjectDiff(
  projectId: string | undefined,
  base: string | undefined,
  target: string | undefined,
): UseQueryResult<ProjectDiff, Error> {
  const enabled =
    typeof projectId === "string" &&
    projectId.length > 0 &&
    typeof base === "string" &&
    base.length > 0 &&
    typeof target === "string" &&
    target.length > 0;

  return useQuery({
    queryKey: projectDiffKey(projectId ?? "", base ?? "", target ?? ""),
    enabled,
    queryFn: () =>
      getProjectDiff(projectId as string, {
        base: base as string,
        target: target as string,
      }),
    staleTime: 30_000,
    // Keep the previous diff on screen while a new base/target combination is
    // in flight so swapping releases doesn't flash an empty skeleton.
    placeholderData: keepPreviousData,
  });
}
