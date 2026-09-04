// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * useUpdateFindingAssignment — ER28b.
 *
 * PATCH /v1/vulnerability_findings/{id}/assignment.
 *
 * Deliberately NOT optimistic, unlike `useUpdateVulnerabilityStatus`. That
 * hook predicts the new status and patches every list cache shape, which it
 * has to because a status badge that lags a click feels broken. Assignment
 * has no such prediction to make: the server decides which deadline governs
 * and whether the one just typed is being ignored, and guessing that here
 * would put the precedence rule in a second place.
 *
 * So the server's response is written into the detail cache and the lists are
 * invalidated. The save is a deliberate act behind a button, not a toggle, so
 * the round trip is affordable.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { vulnerabilitiesKeyPrefix } from "@/features/projects/api/useVulnerabilities";
import { vulnerabilityKey } from "@/features/projects/api/useVulnerability";
import {
  updateFindingAssignment,
  type UpdateFindingAssignmentBody,
  type VulnerabilityDetail,
} from "@/features/projects/api/vulnerabilitiesApi";

interface Variables {
  findingId: string;
  body: UpdateFindingAssignmentBody;
}

export function useUpdateFindingAssignment(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation<VulnerabilityDetail, unknown, Variables>({
    mutationFn: ({ findingId, body }) => updateFindingAssignment(findingId, body),
    onSuccess: (detail, { findingId }) => {
      // The post-commit payload is the single source of truth: it carries the
      // effective deadline, which one governs, and whether a date just typed
      // is being ignored. Recomputing any of that here would duplicate the
      // rule that decides it.
      queryClient.setQueryData(vulnerabilityKey(findingId), detail);
      // The row's owner badge and the "mine" filter both change, so the list
      // has to come from the server rather than be patched from here.
      //
      // Built from `vulnerabilitiesKeyPrefix`, not written out here. Writing
      // the shape again would be a second copy of it, and a key that stops
      // matching invalidates nothing without failing: the mutation succeeds,
      // the drawer refreshes from its own cache write, and only the table is
      // stale, which reads as the list being wrong rather than this being
      // wrong. This mutation shipped that bug once already.
      void queryClient.invalidateQueries({
        queryKey: vulnerabilitiesKeyPrefix(projectId),
      });
    },
  });
}
