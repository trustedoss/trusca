/**
 * TanStack Query hook for the admin System-Health summary — Phase 4 PR #14.
 * Polls every 30s by default (CLAUDE.md "Realtime").
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getAdminHealth,
  type SystemHealthOut,
} from "@/features/admin/health/api/adminHealthApi";

export function adminHealthQueryKey() {
  return ["admin", "health"] as const;
}

export function useAdminHealth(options?: {
  refetchIntervalMs?: number | false;
}): UseQueryResult<SystemHealthOut, Error> {
  return useQuery({
    queryKey: adminHealthQueryKey(),
    queryFn: () => getAdminHealth(),
    refetchInterval: options?.refetchIntervalMs ?? 30_000,
  });
}
