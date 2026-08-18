// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Queries and mutations for transition approvals.
 *
 * Deciding invalidates the finding as well as the queue. The decision moves
 * the finding, so a queue that refreshed alone would leave the vulnerability
 * screen showing the old status until something else happened to refetch it,
 * and the approver would reasonably read that as the approval not working.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type RequestTransitionBody,
  type TransitionApprovalListOut,
  type TransitionApprovalOut,
  decideTransition,
  listPendingTransitions,
  requestTransition,
} from "@/lib/transitionApprovalsApi";

export function pendingTransitionsQueryKey() {
  return ["transition-approvals", "pending"] as const;
}

export function usePendingTransitions(enabled = true) {
  return useQuery<TransitionApprovalListOut, Error>({
    queryKey: pendingTransitionsQueryKey(),
    enabled,
    queryFn: () => listPendingTransitions(),
  });
}

export function useRequestTransition() {
  const queryClient = useQueryClient();
  return useMutation<TransitionApprovalOut, Error, RequestTransitionBody>({
    mutationFn: (body) => requestTransition(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: pendingTransitionsQueryKey() });
    },
  });
}

export function useDecideTransition() {
  const queryClient = useQueryClient();
  return useMutation<
    TransitionApprovalOut,
    Error,
    { approvalId: string; approve: boolean; note?: string | null }
  >({
    mutationFn: ({ approvalId, approve, note }) =>
      decideTransition(approvalId, { approve, note }),
    onSuccess: (decided) => {
      void queryClient.invalidateQueries({ queryKey: pendingTransitionsQueryKey() });
      // The finding moved on approval, so its caches are stale too. Invalidated
      // on a rejection as well: cheap, and it keeps the two paths identical
      // rather than making the refresh depend on the outcome.
      void queryClient.invalidateQueries({
        queryKey: ["vulnerability_findings", decided.finding_id],
      });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}
