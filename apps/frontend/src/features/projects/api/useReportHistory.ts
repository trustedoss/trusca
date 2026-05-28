/**
 * useReportHistory — W3 #32 (Reports center, FE half).
 *
 * TanStack Query hook that powers the Reports tab's right-hand activity table.
 * Mirrors `useObligations` (PR #13): plain `useQuery` (not infinite) because the
 * pagination is page-numbered and the table needs to render a "Page N of M"
 * pager rather than a continuous infinite list.
 *
 * `keepPreviousData` keeps the existing rows on-screen while a page-change /
 * filter-change refetch is in flight so the table never collapses to a
 * skeleton between adjacent pages (mirrors NotificationsPage pattern).
 *
 * Query key shape (tuple, prefix-invalidatable):
 *   ["projects", projectId, "reports-history", { types, scanId, page, pageSize }]
 *
 * 401 / 404 / 429 surface as `ProblemError` via the shared axios interceptor
 * (`lib/api.ts`), so consumers just read `query.error` and render the localised
 * fallback. The 404 envelope is existence-hide (cross-team or unknown project
 * is indistinguishable from the wire) — the UI must render a generic message.
 */
import {
  keepPreviousData,
  useQuery,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  fetchReportHistory,
  type ReportHistoryResponse,
  type ReportType,
} from "@/features/projects/api/reportHistoryApi";

export interface UseReportHistoryFilters {
  /** Multi-select type filter. Empty array → all four types. */
  types: ReportType[];
  /** Optional scan id filter. */
  scanId?: string;
  /** 1-based page number. */
  page: number;
  /** Rows per page. */
  pageSize: number;
}

export function reportHistoryKey(
  projectId: string,
  filters: UseReportHistoryFilters,
) {
  return [
    "projects",
    projectId,
    "reports-history",
    {
      types: [...filters.types].sort(),
      scanId: filters.scanId ?? null,
      page: filters.page,
      pageSize: filters.pageSize,
    },
  ] as const;
}

export function useReportHistory(
  projectId: string | undefined,
  filters: UseReportHistoryFilters,
): UseQueryResult<ReportHistoryResponse, Error> {
  return useQuery({
    queryKey: reportHistoryKey(projectId ?? "", filters),
    enabled: typeof projectId === "string" && projectId.length > 0,
    queryFn: () =>
      fetchReportHistory(projectId as string, {
        types: filters.types.length ? filters.types : undefined,
        scanId: filters.scanId,
        page: filters.page,
        pageSize: filters.pageSize,
      }),
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}
