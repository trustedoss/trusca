/**
 * useObligations — Phase 3 PR #13.
 *
 * Paginated query for the project's obligations. Mirrors `useLicenses` (PR
 * #12): `useQuery` (not `useInfiniteQuery`) because the read is read-only
 * and the distribution payload only makes sense per filter slice; flattening
 * pages would muddle the chart semantics.
 */
import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";

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
  offset: number;
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
      offset: filters.offset,
      scanId: filters.scanId ?? null,
    },
  ] as const;
}

export function useObligations(
  projectId: string | undefined,
  filters: ObligationsQueryFilters,
): UseQueryResult<ObligationListResponse, Error> {
  return useQuery({
    queryKey: obligationsKey(projectId ?? "", filters),
    enabled: typeof projectId === "string" && projectId.length > 0,
    queryFn: () =>
      listProjectObligations(projectId as string, {
        limit: filters.limit,
        offset: filters.offset,
        search: filters.search.trim() || undefined,
        kinds: filters.kinds.length ? filters.kinds : undefined,
        categories: filters.categories.length ? filters.categories : undefined,
        sort: filters.sort,
        order: filters.order,
        scanId: filters.scanId,
      }),
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}
