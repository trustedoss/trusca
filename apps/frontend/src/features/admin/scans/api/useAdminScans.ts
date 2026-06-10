/**
 * TanStack Query hooks for the admin Scan Queue surface — Phase 4 PR #14.
 *
 * The list query polls every 30s by default — the queue is a live signal
 * that the operator wants to react to within the same minute. The cancel
 * mutation invalidates the entire `["admin", "scans"]` prefix so every
 * tab (running / queued / failed / all) refreshes from the next page-turn.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  cancelAdminScan,
  listAdminScans,
  type AdminScanListItem,
  type AdminScanListPage,
  type AdminScanListParams,
} from "@/features/admin/scans/api/adminScansApi";

export function adminScansQueryKey(params: AdminScanListParams) {
  return [
    "admin",
    "scans",
    {
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
      status: params.status ?? null,
      kind: params.kind ?? null,
      project: params.project ?? null,
    },
  ] as const;
}

export function useAdminScans(
  params: AdminScanListParams,
  options?: { refetchIntervalMs?: number | false },
): UseQueryResult<AdminScanListPage, Error> {
  return useQuery({
    queryKey: adminScansQueryKey(params),
    queryFn: () => listAdminScans(params),
    refetchInterval: options?.refetchIntervalMs ?? 30_000,
  });
}

export function useCancelAdminScan() {
  const queryClient = useQueryClient();
  return useMutation<AdminScanListItem, Error, { scanId: string }>({
    mutationFn: ({ scanId }) => cancelAdminScan(scanId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "scans"] });
    },
  });
}
