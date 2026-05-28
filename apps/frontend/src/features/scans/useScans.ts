/**
 * useScans — Phase 3 / Step 4-C.
 *
 * TanStack Query hook for the cross-project scan queue. The list refetches
 * every 30 s so the queue stays roughly live without us standing up a full
 * WebSocket fan-out (single-scan progress is already handled by
 * `useScanWebSocket`). The query key includes the entire filter tuple so a
 * tab switch transparently drives a refetch.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  listMyScans,
  type ListMyScansParams,
  type ScanListResponse,
} from "@/lib/projectsApi";

export function scansQueryKey(params: ListMyScansParams) {
  return [
    "scans",
    "list",
    {
      status: params.status ?? null,
      page: params.page ?? 1,
      size: params.size ?? 20,
    },
  ] as const;
}

export function useScans(
  params: ListMyScansParams,
  options?: { refetchIntervalMs?: number | false },
): UseQueryResult<ScanListResponse, Error> {
  return useQuery({
    queryKey: scansQueryKey(params),
    queryFn: () => listMyScans(params),
    refetchInterval: options?.refetchIntervalMs ?? 30_000,
  });
}
