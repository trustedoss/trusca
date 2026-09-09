// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useQuery } from "@tanstack/react-query";

import { listGroups, type ListGroupsParams } from "@/features/groups/api/groupsApi";

/**
 * `GET /v1/groups` — flat search (``q``) or one-level drill-down
 * (``parent_id``). Query key is the full param tuple (CLAUDE.md "서버 상태"
 * convention) so search, drill level, and page each get their own cache
 * entry rather than clobbering one another.
 */
export function useGroups(params: ListGroupsParams) {
  return useQuery({
    queryKey: ["groups", params],
    queryFn: () => listGroups(params),
    staleTime: 30_000,
    placeholderData: (previous) => previous,
  });
}
