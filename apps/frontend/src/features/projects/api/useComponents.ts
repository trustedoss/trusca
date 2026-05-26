/**
 * useComponents — Phase 3 PR #10.
 *
 * Infinite-cursor query for the project's components list. Powers the
 * virtualized table in `ComponentsTab`. Each page is `limit` rows starting
 * at `offset`; we stitch them together with `useInfiniteQuery` and the
 * caller flattens `data.pages` for `<TableVirtuoso />`.
 *
 * Query key includes the entire filter tuple so a filter / sort change
 * naturally invalidates the cached pages and starts fresh from offset 0.
 */
import {
  useInfiniteQuery,
  type UseInfiniteQueryResult,
} from "@tanstack/react-query";

import {
  listProjectComponents,
  type ComponentListResponse,
  type ComponentSeverity,
  type ComponentSortKey,
  type LicenseCategoryName,
  type SortOrder,
} from "@/features/projects/api/projectDetailApi";

export interface ComponentsQueryFilters {
  search: string;
  severity: ComponentSeverity[];
  license_category: LicenseCategoryName[];
  sort: ComponentSortKey;
  order: SortOrder;
  pageSize: number;
  /**
   * Pin the list to a specific succeeded scan (feature #28 snapshot anchoring).
   * `undefined` → latest succeeded scan. Part of the cache key so flipping the
   * pinned snapshot refetches from offset 0.
   */
  scanId?: string;
}

export function componentsKey(
  projectId: string,
  filters: ComponentsQueryFilters,
) {
  // The tuple is the cache key — query key stability matters for invalidation.
  // Sort the array filters to keep order-insensitive identity.
  return [
    "projects",
    projectId,
    "components",
    {
      search: filters.search,
      severity: [...filters.severity].sort(),
      license_category: [...filters.license_category].sort(),
      sort: filters.sort,
      order: filters.order,
      pageSize: filters.pageSize,
      scanId: filters.scanId ?? null,
    },
  ] as const;
}

export function useComponents(
  projectId: string | undefined,
  filters: ComponentsQueryFilters,
): UseInfiniteQueryResult<{ pages: ComponentListResponse[] }, Error> {
  return useInfiniteQuery({
    queryKey: componentsKey(projectId ?? "", filters),
    enabled: typeof projectId === "string" && projectId.length > 0,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      listProjectComponents(projectId as string, {
        limit: filters.pageSize,
        offset: pageParam as number,
        search: filters.search.trim() || undefined,
        severity: filters.severity.length ? filters.severity : undefined,
        license_category: filters.license_category.length
          ? filters.license_category
          : undefined,
        sort: filters.sort,
        order: filters.order,
        scanId: filters.scanId,
      }),
    getNextPageParam: (lastPage) => {
      const consumed = lastPage.offset + lastPage.items.length;
      return consumed < lastPage.total ? consumed : undefined;
    },
  });
}
