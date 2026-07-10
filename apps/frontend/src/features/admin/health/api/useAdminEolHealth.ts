/**
 * TanStack Query hook for the admin EOL snapshot panel — Phase M / PR M-3.
 *
 * Polls every 60s, matching the sibling Trivy / KEV panels: the EOL beat is
 * weekly, so the poll exists only to keep the panel honest after a manual
 * task run without a page reload.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getAdminEolHealth,
  type EolStatus,
} from "@/features/admin/health/api/adminEolHealthApi";

export function adminEolHealthQueryKey() {
  return ["admin", "eol", "health"] as const;
}

export function useAdminEolHealth(options?: {
  refetchIntervalMs?: number | false;
}): UseQueryResult<EolStatus, Error> {
  return useQuery({
    queryKey: adminEolHealthQueryKey(),
    queryFn: () => getAdminEolHealth(),
    refetchInterval: options?.refetchIntervalMs ?? 60_000,
  });
}
