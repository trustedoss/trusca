/**
 * TanStack Query hook for the admin KEV feed panel — Phase C / C2.
 *
 * Polls every 60s, matching `useAdminTrivyHealth`: the KEV sync beat runs on
 * an hours-scale cadence, so a faster poll would only re-read unchanged
 * state. 60s keeps the panel responsive right after a manual sync without
 * thrashing the endpoint.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getAdminKevHealth,
  type KevFeedStatus,
} from "@/features/admin/health/api/adminKevHealthApi";

export function adminKevHealthQueryKey() {
  return ["admin", "kev", "health"] as const;
}

export function useAdminKevHealth(options?: {
  refetchIntervalMs?: number | false;
}): UseQueryResult<KevFeedStatus, Error> {
  return useQuery({
    queryKey: adminKevHealthQueryKey(),
    queryFn: () => getAdminKevHealth(),
    refetchInterval: options?.refetchIntervalMs ?? 60_000,
  });
}
