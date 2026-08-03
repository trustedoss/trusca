// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  createSavedSearch,
  deleteSavedSearch,
  fetchSearchResults,
  listSavedSearches,
  type SavedSearchListResponse,
  type SearchResultsPage,
  type SearchResultsParams,
} from "@/features/search/api/searchResultsApi";

/** Rows per page. Matches the paginated-table surfaces elsewhere. */
export const SEARCH_PAGE_SIZE = 25;

/** Below this the server returns an empty page, so do not spend a request. */
export const SEARCH_MIN_CHARS = 2;

export const savedSearchesQueryKey = ["saved-searches"] as const;

/**
 * Cache key for a results query. Array filters are sorted so two selections
 * with the same members but different click order share one entry.
 */
export function searchResultsQueryKey(params: SearchResultsParams) {
  return [
    "search-results",
    {
      kind: params.kind,
      q: params.q,
      page: params.page ?? 1,
      size: params.size ?? SEARCH_PAGE_SIZE,
      severity: [...(params.severity ?? [])].sort(),
      status: [...(params.status ?? [])].sort(),
      packageType: [...(params.packageType ?? [])].sort(),
      licenseCategory: [...(params.licenseCategory ?? [])].sort(),
    },
  ] as const;
}

export function useSearchResults(
  params: SearchResultsParams,
): UseQueryResult<SearchResultsPage, Error> {
  return useQuery({
    queryKey: searchResultsQueryKey(params),
    queryFn: () => fetchSearchResults(params),
    enabled: params.q.trim().length >= SEARCH_MIN_CHARS,
    staleTime: 30_000,
    // Keep the previous page on screen while the next one loads: a table that
    // blanks between pages reads as "no results" for a moment.
    placeholderData: keepPreviousData,
  });
}

export function useSavedSearches(): UseQueryResult<
  SavedSearchListResponse,
  Error
> {
  return useQuery({
    queryKey: savedSearchesQueryKey,
    queryFn: listSavedSearches,
    staleTime: 30_000,
  });
}

export function useCreateSavedSearch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createSavedSearch,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: savedSearchesQueryKey });
    },
  });
}

export function useDeleteSavedSearch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteSavedSearch,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: savedSearchesQueryKey });
    },
  });
}
