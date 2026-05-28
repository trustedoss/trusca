/**
 * useReleases — feature #28 Phase 1 (release snapshot viewing).
 *
 * Paginated query for the project's release snapshots (its succeeded scans),
 * newest-first. Powers the Releases tab table and — because the list is
 * newest-first — also resolves "which scan is the latest succeeded one" so the
 * detail page can decide whether a pinned `?scan=` is the latest (normal) or an
 * older snapshot (historical, read-only).
 *
 * Query key is `["projects", projectId, "releases", { page, size }]` so it
 * shares the `["projects", projectId]` invalidation prefix with the rest of the
 * project detail surface — a fresh scan refreshes the release list alongside
 * the overview / components / vuln queries.
 */
import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  listProjectReleases,
  type ReleaseListResponse,
} from "@/features/projects/api/releasesApi";

export interface ReleasesQueryFilters {
  page: number;
  size: number;
}

export function releasesKey(projectId: string, filters: ReleasesQueryFilters) {
  return [
    "projects",
    projectId,
    "releases",
    { page: filters.page, size: filters.size },
  ] as const;
}

export function useReleases(
  projectId: string | undefined,
  filters: ReleasesQueryFilters,
): UseQueryResult<ReleaseListResponse, Error> {
  return useQuery({
    queryKey: releasesKey(projectId ?? "", filters),
    enabled: typeof projectId === "string" && projectId.length > 0,
    queryFn: () =>
      listProjectReleases(projectId as string, {
        page: filters.page,
        size: filters.size,
      }),
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}
