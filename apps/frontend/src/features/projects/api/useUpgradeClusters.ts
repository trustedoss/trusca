/**
 * useUpgradeClusters — W9-#53 "Group by upgrade".
 *
 * Sibling of {@link useVulnerabilities}: fetches the project's minimum-safe-
 * upgrade clusters (`GET /v1/projects/{id}/vulnerabilities/upgrade-clusters`)
 * for the Vulnerabilities tab's "By upgrade" grouping. Unlike the flat list
 * this endpoint is NOT paginated and takes no filters — it returns every
 * cluster for the resolved scan snapshot, pre-sorted most-actionable first.
 *
 * The `enabled` gate is threaded from the tab's group-by mode so exactly one
 * of {flat list, upgrade clusters} query runs at a time: in "flat" mode this
 * query is disabled (the flat list's query is the only one in flight), and in
 * "upgrade" mode the flat query is disabled instead. `scanId` threads the same
 * release-snapshot anchor the flat list uses, and is part of the cache key so
 * flipping the pinned snapshot refetches.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  listUpgradeClusters,
  type UpgradeClusterListResponse,
} from "@/features/projects/api/vulnerabilitiesApi";

export interface UseUpgradeClustersOptions {
  /** Pinned snapshot scan id (feature #28). `undefined` → latest succeeded. */
  scanId?: string;
  /**
   * Gate the query on the tab's group-by mode. `false` in "flat" mode so the
   * network call only fires when the user actually switches to "By upgrade".
   */
  enabled?: boolean;
}

export function upgradeClustersKey(
  projectId: string,
  scanId: string | undefined,
) {
  return [
    "projects",
    projectId,
    "upgrade-clusters",
    { scanId: scanId ?? null },
  ] as const;
}

export function useUpgradeClusters(
  projectId: string | undefined,
  { scanId, enabled = true }: UseUpgradeClustersOptions = {},
): UseQueryResult<UpgradeClusterListResponse, Error> {
  return useQuery({
    queryKey: upgradeClustersKey(projectId ?? "", scanId),
    enabled:
      enabled && typeof projectId === "string" && projectId.length > 0,
    queryFn: () =>
      listUpgradeClusters(projectId as string, { scanId }),
  });
}
