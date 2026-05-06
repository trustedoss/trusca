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

import {
  listProjectVulnerabilities,
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
  limit: number;
  offset: number;
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
      limit: filters.limit,
      offset: filters.offset,
    },
  ] as const;
}

export function useVulnerabilities(
  projectId: string | undefined,
  filters: VulnerabilitiesQueryFilters,
): UseQueryResult<VulnerabilityListResponse, Error> {
  return useQuery({
    queryKey: vulnerabilitiesKey(projectId ?? "", filters),
    enabled: typeof projectId === "string" && projectId.length > 0,
    queryFn: () =>
      listProjectVulnerabilities(projectId as string, {
        limit: filters.limit,
        offset: filters.offset,
        search: filters.search.trim() || undefined,
        severity: filters.severity.length ? filters.severity : undefined,
        status: filters.status.length ? filters.status : undefined,
        sort: filters.sort,
        order: filters.order,
      }),
    placeholderData: keepPreviousData,
  });
}
