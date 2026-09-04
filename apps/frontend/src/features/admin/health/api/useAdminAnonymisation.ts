// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * TanStack Query hook for the anonymisation backlog panel (ER32).
 *
 * Polls every 60s like the other health panels. Nothing here changes on its
 * own: the list only moves when a super admin approves a request or an
 * operator runs the command, both human acts. The poll exists so an operator
 * who approves in one tab sees the obligation appear in the other.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getAwaitingExecution,
  type AwaitingExecutionList,
} from "@/features/admin/health/api/adminAnonymisationApi";

export function adminAnonymisationQueryKey() {
  return ["admin", "anonymisation", "awaiting-execution"] as const;
}

export function useAdminAnonymisation(options?: {
  refetchIntervalMs?: number | false;
}): UseQueryResult<AwaitingExecutionList, Error> {
  return useQuery({
    queryKey: adminAnonymisationQueryKey(),
    queryFn: () => getAwaitingExecution(),
    refetchInterval: options?.refetchIntervalMs ?? 60_000,
  });
}
