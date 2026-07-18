/**
 * useVulnerabilities — Phase 3 PR #11.
 *
 * Paginated query for the project's vulnerability findings list. Powers the
 * virtualized table in `VulnerabilitiesTab`. Uses `useQuery` with a single
 * page (offset/limit) instead of `useInfiniteQuery` so the optimistic
 * status-update mutation can write a single response back into the cache
 * without flattening pages — pagination is via offset, not cursor.
 *
 * Query key includes the entire filter tuple so a filter / sort change
 * naturally invalidates the cached page. `keepPreviousData` keeps the table
 * stable while a new page is in flight.
 */
import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";

import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";
import {
  listProjectVulnerabilities,
  type ReachabilityFilter,
  type SortOrder,
  type VulnFindingStatus,
  type VulnSeverity,
  type VulnerabilityListResponse,
  type VulnerabilitySortKey,
} from "@/features/projects/api/vulnerabilitiesApi";

export interface VulnerabilitiesQueryFilters {
  search: string;
  severity: VulnSeverity[];
  status: VulnFindingStatus[];
  sort: VulnerabilitySortKey;
  order: SortOrder;
  /**
   * EPSS threshold (0–1), or `null` for "no threshold". When set, the backend
   * keeps findings with `epss_score >= min_epss` and drops NULL-EPSS rows.
   */
  min_epss: number | null;
  /**
   * Tri-state reachability filter (v2.3 r2), or `null` for "no filter".
   * `"true"` / `"false"` / `"unknown"` keep reachable / proven-unreachable /
   * not-analysed findings respectively.
   */
  reachable: ReachabilityFilter | null;
  /**
   * License-category buckets to keep (W2 #33). Empty array = no filter (all
   * categories). Members are the four `LicenseCategoryName` tokens; the
   * "unknown" bucket also covers findings whose component has no license
   * finding (the backend joins LEFT and falls back to "unknown").
   */
  license_category: LicenseCategoryName[];
  limit: number;
  offset: number;
  /**
   * Pin the list to a specific succeeded scan (feature #28 snapshot anchoring).
   * `undefined` → latest succeeded scan. Part of the cache key so flipping the
   * pinned snapshot refetches.
   */
  scanId?: string;
}

export function vulnerabilitiesKey(
  projectId: string,
  filters: VulnerabilitiesQueryFilters,
) {
  // Sort the array filters to keep order-insensitive identity. The query
  // client compares keys structurally, so [crit,high] and [high,crit] would
  // otherwise produce two cache entries.
  return [
    "projects",
    projectId,
    "vulnerabilities",
    {
      search: filters.search,
      severity: [...filters.severity].sort(),
      status: [...filters.status].sort(),
      sort: filters.sort,
      order: filters.order,
      min_epss: filters.min_epss,
      reachable: filters.reachable,
      license_category: [...filters.license_category].sort(),
      limit: filters.limit,
      offset: filters.offset,
      scanId: filters.scanId ?? null,
    },
  ] as const;
}

export interface UseVulnerabilitiesOptions {
  /**
   * Gate the query on the tab's group-by mode (W9-#53). `false` in "upgrade"
   * mode so only the upgrade-clusters query is in flight there; defaults to
   * `true` so existing callers keep the always-on behavior.
   */
  enabled?: boolean;
}

export function useVulnerabilities(
  projectId: string | undefined,
  filters: VulnerabilitiesQueryFilters,
  { enabled = true }: UseVulnerabilitiesOptions = {},
): UseQueryResult<VulnerabilityListResponse, Error> {
  return useQuery({
    queryKey: vulnerabilitiesKey(projectId ?? "", filters),
    enabled:
      enabled && typeof projectId === "string" && projectId.length > 0,
    queryFn: () =>
      listProjectVulnerabilities(projectId as string, {
        limit: filters.limit,
        offset: filters.offset,
        search: filters.search.trim() || undefined,
        severity: filters.severity.length ? filters.severity : undefined,
        status: filters.status.length ? filters.status : undefined,
        sort: filters.sort,
        order: filters.order,
        min_epss: filters.min_epss ?? undefined,
        reachable: filters.reachable ?? undefined,
        license_category: filters.license_category.length
          ? filters.license_category
          : undefined,
        scanId: filters.scanId,
      }),
    placeholderData: keepPreviousData,
  });
}
