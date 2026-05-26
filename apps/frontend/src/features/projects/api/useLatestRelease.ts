/**
 * useLatestRelease — feature #28 Phase 1 (release snapshot viewing).
 *
 * Resolves the project's *latest succeeded scan id* by reading the first page
 * (size 1) of the newest-first releases list. The detail page uses this to
 * decide whether a pinned `?scan=` is the latest snapshot (normal view) or an
 * older one (historical, read-only banner + write-control gating).
 *
 * This is a separate, cheap query (size 1) from the Releases tab's paginated
 * list so the banner decision never depends on the tab being mounted — the user
 * can deep-link straight into `/projects/:id?scan=...&tab=vulnerabilities` and
 * still get the correct historical verdict.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  listProjectReleases,
  type ReleaseSnapshot,
} from "@/features/projects/api/releasesApi";

export function latestReleaseKey(projectId: string) {
  return ["projects", projectId, "releases", "latest"] as const;
}

/**
 * Returns the newest release snapshot (the latest succeeded scan), or `null`
 * when the project has no succeeded scan yet. `undefined` while loading.
 */
export function useLatestRelease(
  projectId: string | undefined,
): UseQueryResult<ReleaseSnapshot | null, Error> {
  return useQuery({
    queryKey: latestReleaseKey(projectId ?? ""),
    enabled: typeof projectId === "string" && projectId.length > 0,
    queryFn: async () => {
      const page = await listProjectReleases(projectId as string, {
        page: 1,
        size: 1,
      });
      return page.items[0] ?? null;
    },
    staleTime: 30_000,
  });
}
