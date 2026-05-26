/**
 * useLicenses — Phase 3 PR #12.
 *
 * Paginated query for the project's license findings list. Powers the
 * virtualized table in `LicensesTab`.
 *
 * Why `useQuery` (not `useInfiniteQuery`):
 *   - License findings are read-only (no PATCH) so we never need optimistic
 *     cache writes that would have to reconcile across cursor pages.
 *   - Distribution counts come back inside the same response and only make
 *     sense for the active filter slice; flattening pages would muddle the
 *     "total per category for the visible cut" semantics.
 *
 * Query key includes the entire filter tuple — a filter or sort change
 * naturally invalidates the cache and refetches. `keepPreviousData` keeps
 * the table stable while a new page loads.
 */
import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  listProjectLicenses,
  type LicenseCategoryName,
  type LicenseFindingKind,
  type LicenseListResponse,
  type LicenseSortKey,
  type SortOrder,
} from "@/features/projects/api/licensesApi";

export interface LicensesQueryFilters {
  search: string;
  categories: LicenseCategoryName[];
  kinds: LicenseFindingKind[];
  sort: LicenseSortKey;
  order: SortOrder;
  limit: number;
  offset: number;
  /**
   * Pin the list to a specific succeeded scan (feature #28 snapshot anchoring).
   * `undefined` → latest succeeded scan.
   */
  scanId?: string;
}

export function licensesKey(
  projectId: string,
  filters: LicensesQueryFilters,
) {
  // Sort the array filters to keep order-insensitive identity. The query
  // client compares keys structurally, so [allowed, forbidden] and
  // [forbidden, allowed] would otherwise produce two cache entries.
  return [
    "projects",
    projectId,
    "licenses",
    {
      search: filters.search,
      categories: [...filters.categories].sort(),
      kinds: [...filters.kinds].sort(),
      sort: filters.sort,
      order: filters.order,
      limit: filters.limit,
      offset: filters.offset,
      scanId: filters.scanId ?? null,
    },
  ] as const;
}

export function useLicenses(
  projectId: string | undefined,
  filters: LicensesQueryFilters,
): UseQueryResult<LicenseListResponse, Error> {
  return useQuery({
    queryKey: licensesKey(projectId ?? "", filters),
    enabled: typeof projectId === "string" && projectId.length > 0,
    queryFn: () =>
      listProjectLicenses(projectId as string, {
        limit: filters.limit,
        offset: filters.offset,
        search: filters.search.trim() || undefined,
        categories: filters.categories.length ? filters.categories : undefined,
        kinds: filters.kinds.length ? filters.kinds : undefined,
        sort: filters.sort,
        order: filters.order,
        scanId: filters.scanId,
      }),
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}
