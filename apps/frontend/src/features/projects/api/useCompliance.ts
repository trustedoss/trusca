// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * useCompliance: W9-#58, infinite from A3.
 *
 * Infinite-offset query for the project's unified compliance grid. Powers the
 * single table in the redesigned ComplianceTab: each page is `limit` rows from
 * `offset`, and the caller flattens `data.pages` for the virtual list.
 *
 * It was a single page, with a `?compliance_page=` parameter nothing ever
 * incremented, so the 101st licence finding could not be reached.
 *
 * The query key includes the entire filter tuple, so a filter or sort change
 * naturally invalidates the cached pages and refetches from offset 0.
 */
import {
  useInfiniteQuery,
  type UseInfiniteQueryResult,
} from "@tanstack/react-query";

import {
  listProjectCompliance,
  type ComplianceListResponse,
  type ComplianceSortKey,
  type LicenseCategoryName,
  type ConflictVerdictName,
  type SortOrder,
} from "@/features/projects/api/complianceApi";

export interface ComplianceQueryFilters {
  search: string;
  categories: LicenseCategoryName[];
  kinds: string[];
  hasObligations: boolean | null;
  /** Outbound-license conflict verdict (gap #27). `undefined` → no filter. */
  conflict?: ConflictVerdictName;
  sort: ComplianceSortKey;
  order: SortOrder;
  limit: number;
  /**
   * Pin the list to a specific succeeded scan (feature #28 snapshot anchoring).
   * `undefined` → latest succeeded scan.
   */
  scanId?: string;
}

export function complianceKey(
  projectId: string,
  filters: ComplianceQueryFilters,
) {
  // Sort array filters to keep order-insensitive identity. The query client
  // compares keys structurally, so `[allowed, forbidden]` and
  // `[forbidden, allowed]` would otherwise produce two cache entries.
  return [
    "projects",
    projectId,
    "compliance",
    {
      search: filters.search,
      categories: [...filters.categories].sort(),
      kinds: [...filters.kinds].sort(),
      hasObligations: filters.hasObligations,
      conflict: filters.conflict ?? null,
      sort: filters.sort,
      order: filters.order,
      limit: filters.limit,
      scanId: filters.scanId ?? null,
    },
  ] as const;
}

export function useCompliance(
  projectId: string | undefined,
  filters: ComplianceQueryFilters,
): UseInfiniteQueryResult<{ pages: ComplianceListResponse[] }, Error> {
  return useInfiniteQuery({
    queryKey: complianceKey(projectId ?? "", filters),
    enabled: typeof projectId === "string" && projectId.length > 0,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      listProjectCompliance(projectId as string, {
        limit: filters.limit,
        offset: pageParam as number,
        search: filters.search.trim() || undefined,
        categories: filters.categories.length ? filters.categories : undefined,
        kinds: filters.kinds.length ? filters.kinds : undefined,
        has_obligations:
          filters.hasObligations === null ? undefined : filters.hasObligations,
        conflict: filters.conflict,
        sort: filters.sort,
        order: filters.order,
        scanId: filters.scanId,
      }),
    getNextPageParam: (lastPage) => {
      const consumed = lastPage.offset + lastPage.items.length;
      return consumed < lastPage.total ? consumed : undefined;
    },
    staleTime: 30_000,
  });
}
