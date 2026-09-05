// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * useAssignableMembers - ER28b.
 *
 * The people a finding in this project may be handed to. Read-only and
 * rarely changing, so it is cached per project and only fetched when the
 * assignment editor is actually open.
 */
import { useQuery } from "@tanstack/react-query";

import {
  fetchAssignableMembers,
  type AssignableMemberList,
} from "@/features/projects/api/vulnerabilitiesApi";

export const assignableMembersKey = (projectId: string) =>
  ["projects", projectId, "assignable-members"] as const;

export function useAssignableMembers(projectId: string, enabled = true) {
  return useQuery<AssignableMemberList>({
    queryKey: assignableMembersKey(projectId),
    queryFn: () => fetchAssignableMembers(projectId),
    enabled: enabled && Boolean(projectId),
    staleTime: 5 * 60 * 1000,
  });
}
