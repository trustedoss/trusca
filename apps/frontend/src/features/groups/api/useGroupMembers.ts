// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useQuery } from "@tanstack/react-query";

import { getGroupMembers } from "@/features/groups/api/groupsApi";

/**
 * `GET /v1/groups/{id}/members` — direct vs. cascade-inherited members.
 *
 * `enabled` also gates on `active` so the Members tab's query only fires once
 * the tab is actually opened (this route is developer-role, one grade above
 * list/detail, and returns member identities — no reason to fetch it before
 * the reader asks to see it).
 */
export function useGroupMembers(groupId: string | undefined, active: boolean) {
  return useQuery({
    queryKey: ["groups", groupId, "members"],
    queryFn: () => getGroupMembers(groupId as string),
    enabled: typeof groupId === "string" && groupId.length > 0 && active,
    staleTime: 30_000,
  });
}
