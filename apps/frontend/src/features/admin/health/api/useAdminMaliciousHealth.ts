// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * TanStack Query hook for the admin malicious-snapshot panel — #26.
 *
 * Polls every 60s like its siblings: the beat is weekly, so the poll exists
 * to keep the panel honest after a manual run without a page reload.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getAdminMaliciousHealth,
  type MaliciousStatus,
} from "@/features/admin/health/api/adminMaliciousHealthApi";

export function adminMaliciousHealthQueryKey() {
  return ["admin", "malicious", "health"] as const;
}

export function useAdminMaliciousHealth(options?: {
  refetchIntervalMs?: number | false;
}): UseQueryResult<MaliciousStatus, Error> {
  return useQuery({
    queryKey: adminMaliciousHealthQueryKey(),
    queryFn: () => getAdminMaliciousHealth(),
    refetchInterval: options?.refetchIntervalMs ?? 60_000,
  });
}
