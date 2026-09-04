// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * useVulnerabilities: Phase 3 PR #11, infinite from A2.
 *
 * Infinite-offset query for the project's vulnerability findings list. Powers
 * the virtualized table in `VulnerabilitiesTab`: each page is `limit` rows
 * starting at `offset`, stitched together by `useInfiniteQuery`, and the
 * caller flattens `data.pages` for `<Virtuoso />`.
 *
 * It began as a single-page `useQuery`, so the optimistic status-update
 * mutation could write one response back into the cache without flattening
 * pages. That kept the mutation simple and left the table with no way to
 * reach row 101: the `?page=` parameter existed but nothing incremented it.
 * The mutation now understands both cache shapes (see
 * `useUpdateVulnerabilityStatus`), which is the cost of the row being
 * reachable at all.
 *
 * Query key includes the entire filter tuple so a filter / sort change
 * naturally invalidates the cached pages and starts fresh from offset 0.
 */
import {
  useInfiniteQuery,
  type UseInfiniteQueryResult,
} from "@tanstack/react-query";

import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";
import {
  listProjectVulnerabilities,
  type AssigneeFilter,
  type ReachabilityFilter,
  type SlaFilter,
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
   * SLA-status filter (X1 SLA/aging), or `null` for "no filter". Single
   * token — keeps only findings whose server-computed `sla_status` matches.
   */
  sla: SlaFilter | null;
  /**
   * Ownership filter (ER28b). `"me"` is the caller's own findings, and
   * `"unassigned"` the ones nobody has taken; `null` disables it.
   *
   * Two tokens rather than a user id, because a developer can only assign to
   * themselves: no endpoint lets them enumerate their team, so an id would
   * add a way to ask which findings a named person owns that no screen needs.
   *
   * Keyed on the finding's `assignee_user_id` column server-side. NOT on
   * `assignee_is_active`, which looks equivalent and is not: filtering on
   * that would hide findings owned by a deactivated account, which is exactly
   * the state the list has to make visible.
   */
  assignee: AssigneeFilter | null;
  /**
   * License-category buckets to keep (W2 #33). Empty array = no filter (all
   * categories). Members are the four `LicenseCategoryName` tokens; the
   * "unknown" bucket also covers findings whose component has no license
   * finding (the backend joins LEFT and falls back to "unknown").
   */
  license_category: LicenseCategoryName[];
  limit: number;
  /**
   * Pin the list to a specific succeeded scan (feature #28 snapshot anchoring).
   * `undefined` → latest succeeded scan. Part of the cache key so flipping the
   * pinned snapshot refetches.
   */
  scanId?: string;
}

/**
 * The part of the list key that identifies the project's vulnerability list,
 * without the filters.
 *
 * Exported so callers that need to invalidate the whole list can build the
 * prefix from HERE rather than writing `["projects", id, "vulnerabilities"]`
 * out again. A hand-written copy is not a compile error and not a runtime
 * error: a key that does not match invalidates nothing, silently, so the
 * copy would go stale without anything failing. ER28b's assignment mutation
 * had exactly that bug before this existed.
 */
export function vulnerabilitiesKeyPrefix(projectId: string) {
  return ["projects", projectId, "vulnerabilities"] as const;
}

export function vulnerabilitiesKey(
  projectId: string,
  filters: VulnerabilitiesQueryFilters,
) {
  // Sort the array filters to keep order-insensitive identity. The query
  // client compares keys structurally, so [crit,high] and [high,crit] would
  // otherwise produce two cache entries.
  return [
    ...vulnerabilitiesKeyPrefix(projectId),
    {
      search: filters.search,
      severity: [...filters.severity].sort(),
      status: [...filters.status].sort(),
      sort: filters.sort,
      order: filters.order,
      min_epss: filters.min_epss,
      reachable: filters.reachable,
      sla: filters.sla,
      assignee: filters.assignee,
      license_category: [...filters.license_category].sort(),
      limit: filters.limit,
      scanId: filters.scanId ?? null,
    },
  ] as const;
}

export interface UseVulnerabilitiesOptions {
  /**
   * Gate the query on the tab's group-by mode. `false` in "upgrade"
   * mode so only the upgrade-clusters query is in flight there; defaults to
   * `true` so existing callers keep the always-on behavior.
   */
  enabled?: boolean;
}

export function useVulnerabilities(
  projectId: string | undefined,
  filters: VulnerabilitiesQueryFilters,
  { enabled = true }: UseVulnerabilitiesOptions = {},
): UseInfiniteQueryResult<{ pages: VulnerabilityListResponse[] }, Error> {
  return useInfiniteQuery({
    queryKey: vulnerabilitiesKey(projectId ?? "", filters),
    enabled:
      enabled && typeof projectId === "string" && projectId.length > 0,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      listProjectVulnerabilities(projectId as string, {
        limit: filters.limit,
        offset: pageParam as number,
        search: filters.search.trim() || undefined,
        severity: filters.severity.length ? filters.severity : undefined,
        status: filters.status.length ? filters.status : undefined,
        sort: filters.sort,
        order: filters.order,
        min_epss: filters.min_epss ?? undefined,
        reachable: filters.reachable ?? undefined,
        sla: filters.sla ?? undefined,
        assignee: filters.assignee ?? undefined,
        license_category: filters.license_category.length
          ? filters.license_category
          : undefined,
        scanId: filters.scanId,
      }),
    getNextPageParam: (lastPage) => {
      const consumed = lastPage.offset + lastPage.items.length;
      return consumed < lastPage.total ? consumed : undefined;
    },
  });
}
