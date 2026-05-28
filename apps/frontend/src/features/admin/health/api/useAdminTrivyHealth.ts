/**
 * TanStack Query hook for the admin Trivy DB Health panel — W6-#43e.
 *
 * Polls every 60s by default — twice the admin/health cadence (30s) because
 * the on-disk metadata.json only refreshes weekly. The backend service caches
 * for 60s anyway, so a faster poll would hit the cache without surfacing new
 * data; 60s keeps the dashboard responsive without thrashing.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getAdminTrivyHealth,
  type TrivyDbStatus,
} from "@/features/admin/health/api/adminTrivyHealthApi";

export function adminTrivyHealthQueryKey() {
  return ["admin", "trivy", "health"] as const;
}

export function useAdminTrivyHealth(options?: {
  refetchIntervalMs?: number | false;
}): UseQueryResult<TrivyDbStatus, Error> {
  return useQuery({
    queryKey: adminTrivyHealthQueryKey(),
    queryFn: () => getAdminTrivyHealth(),
    refetchInterval: options?.refetchIntervalMs ?? 60_000,
  });
}
