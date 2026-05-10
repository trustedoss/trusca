/**
 * TanStack Query hooks for the admin DT Connector surface — Phase 4 PR #14.
 *
 * Server state lives in TanStack Query (CLAUDE.md "State"). Mutations
 * invalidate the status + orphans prefix so a cleanup or health probe
 * triggers an automatic refresh.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  cleanupDTOrphans,
  forceDTHealthCheck,
  getDTStatus,
  listDTOrphans,
  resetDTBreaker,
  type BreakerResetOut,
  type DTOrphanListPage,
  type DTOrphanListParams,
  type DTStatus,
  type HealthProbeOut,
  type OrphanCleanupEnqueued,
  type OrphanCleanupRequestPayload,
} from "@/features/admin/dt/api/adminDTApi";

export function dtStatusQueryKey() {
  return ["admin", "dt", "status"] as const;
}

export function dtOrphansQueryKey(params: DTOrphanListParams) {
  return [
    "admin",
    "dt",
    "orphans",
    {
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  ] as const;
}

export function useDTStatus(options?: {
  refetchIntervalMs?: number;
}): UseQueryResult<DTStatus, Error> {
  return useQuery({
    queryKey: dtStatusQueryKey(),
    queryFn: () => getDTStatus(),
    // Status panel polls every 30s by default — the breaker state can
    // flip independently of any user action so a steady refresh is the
    // CLAUDE.md "Realtime" expectation for an admin dashboard.
    refetchInterval: options?.refetchIntervalMs ?? 30_000,
  });
}

export function useDTOrphans(
  params: DTOrphanListParams,
): UseQueryResult<DTOrphanListPage, Error> {
  return useQuery({
    queryKey: dtOrphansQueryKey(params),
    queryFn: () => listDTOrphans(params),
  });
}

function invalidateDT(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["admin", "dt"] });
}

export function useCleanupDTOrphans() {
  const queryClient = useQueryClient();
  return useMutation<
    OrphanCleanupEnqueued,
    Error,
    OrphanCleanupRequestPayload | undefined
  >({
    mutationFn: (payload) => cleanupDTOrphans(payload ?? {}),
    onSuccess: () => invalidateDT(queryClient),
  });
}

export function useForceDTHealthCheck() {
  const queryClient = useQueryClient();
  return useMutation<HealthProbeOut, Error, void>({
    mutationFn: () => forceDTHealthCheck(),
    onSuccess: () => invalidateDT(queryClient),
  });
}

export function useResetDTBreaker() {
  const queryClient = useQueryClient();
  return useMutation<BreakerResetOut, Error, void>({
    mutationFn: () => resetDTBreaker(),
    onSuccess: () => invalidateDT(queryClient),
  });
}
