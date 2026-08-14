// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * useObligations — Phase 3 PR #13, infinite from A3.
 *
 * Infinite-offset query for the project's obligations. It was a single page,
 * on the reasoning that the read is read-only and the distribution payload
 * only makes sense per filter slice, so flattening pages would muddle the
 * chart. The chart is fine — every page repeats the same whole-project
 * distribution, and the caller reads it from the first one. What the single
 * page actually cost was the 101st obligation, which no control could reach.
 */
import {
  useInfiniteQuery,
  type UseInfiniteQueryResult,
} from "@tanstack/react-query";

import {
  listProjectObligations,
  type LicenseCategoryName,
  type ObligationListResponse,
  type ObligationSortKey,
  type SortOrder,
} from "@/features/projects/api/obligationsApi";

export interface ObligationsQueryFilters {
  search: string;
  kinds: string[];
  categories: LicenseCategoryName[];
  sort: ObligationSortKey;
  order: SortOrder;
  limit: number;
  /**
   * Pin the list to a specific succeeded scan (feature #28 snapshot anchoring).
   * `undefined` → latest succeeded scan.
   */
  scanId?: string;
}

export function obligationsKey(
  projectId: string,
  filters: ObligationsQueryFilters,
) {
  return [
    "projects",
    projectId,
    "obligations",
    {
      search: filters.search,
      kinds: [...filters.kinds].sort(),
      categories: [...filters.categories].sort(),
      sort: filters.sort,
      order: filters.order,
      limit: filters.limit,
      scanId: filters.scanId ?? null,
    },
  ] as const;
}

export function useObligations(
  projectId: string | undefined,
  filters: ObligationsQueryFilters,
): UseInfiniteQueryResult<{ pages: ObligationListResponse[] }, Error> {
  return useInfiniteQuery({
    queryKey: obligationsKey(projectId ?? "", filters),
    enabled: typeof projectId === "string" && projectId.length > 0,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      listProjectObligations(projectId as string, {
        limit: filters.limit,
        offset: pageParam as number,
        search: filters.search.trim() || undefined,
        kinds: filters.kinds.length ? filters.kinds : undefined,
        categories: filters.categories.length ? filters.categories : undefined,
        sort: filters.sort,
        order: filters.order,
        scanId: filters.scanId,
      }),
    // Counted from the request rather than the response: this endpoint does
    // not echo `offset` back, unlike the components and vulnerabilities lists.
    getNextPageParam: (lastPage, _allPages, lastPageParam) => {
      const consumed = (lastPageParam as number) + lastPage.items.length;
      return consumed < lastPage.total ? consumed : undefined;
    },
    staleTime: 30_000,
  });
}
