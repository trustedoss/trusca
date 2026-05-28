/**
 * useCompliance — W9-#58.
 *
 * Paginated query for the project's unified compliance grid. Powers the
 * single table in the redesigned ComplianceTab.
 *
 * The query key includes the entire filter tuple — a filter / sort change
 * naturally invalidates the cache and refetches. `keepPreviousData` keeps
 * the grid stable while a new page loads.
 */
import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  listProjectCompliance,
  type ComplianceListResponse,
  type ComplianceSortKey,
  type LicenseCategoryName,
  type SortOrder,
} from "@/features/projects/api/complianceApi";

export interface ComplianceQueryFilters {
  search: string;
  categories: LicenseCategoryName[];
  kinds: string[];
  hasObligations: boolean | null;
  sort: ComplianceSortKey;
  order: SortOrder;
  limit: number;
  offset: number;
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
      sort: filters.sort,
      order: filters.order,
      limit: filters.limit,
      offset: filters.offset,
      scanId: filters.scanId ?? null,
    },
  ] as const;
}

export function useCompliance(
  projectId: string | undefined,
  filters: ComplianceQueryFilters,
): UseQueryResult<ComplianceListResponse, Error> {
  return useQuery({
    queryKey: complianceKey(projectId ?? "", filters),
    enabled: typeof projectId === "string" && projectId.length > 0,
    queryFn: () =>
      listProjectCompliance(projectId as string, {
        limit: filters.limit,
        offset: filters.offset,
        search: filters.search.trim() || undefined,
        categories: filters.categories.length ? filters.categories : undefined,
        kinds: filters.kinds.length ? filters.kinds : undefined,
        has_obligations:
          filters.hasObligations === null ? undefined : filters.hasObligations,
        sort: filters.sort,
        order: filters.order,
        scanId: filters.scanId,
      }),
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}
