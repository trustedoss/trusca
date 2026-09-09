// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useQuery } from "@tanstack/react-query";

import { getGroup } from "@/features/groups/api/groupsApi";

/**
 * `GET /v1/groups/{id}` — detail + ancestors + 30-day subtree summary.
 *
 * A 404 (existence-hide: nonexistent or inaccessible, indistinguishable) is
 * NOT retried — the default retry would turn a legitimate "not found" into a
 * multi-second spinner before the not-found state finally renders.
 */
export function useGroupDetail(groupId: string | undefined) {
  return useQuery({
    queryKey: ["groups", groupId, "detail"],
    queryFn: () => getGroup(groupId as string),
    enabled: typeof groupId === "string" && groupId.length > 0,
    staleTime: 30_000,
    retry: (failureCount, error) => {
      const status = (error as { status?: number } | undefined)?.status;
      if (status === 404) return false;
      return failureCount < 2;
    },
  });
}
