// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Writing this project's record against one obligation (N15).
 *
 * The obligation itself stays read-only: it is what a licence asks of
 * everybody who uses it. What a project owns is whether it has done the thing,
 * and that is what these two mutations write.
 *
 * Both invalidate the whole `["projects", id, "obligations"]` subtree rather
 * than patching the cached row. The list carries the record inline and is
 * keyed by every filter combination the user has visited, so a targeted patch
 * would have to find each of those keys and would leave the ones it missed
 * showing the old status.
 */
import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";

import {
  clearObligationFulfilment,
  recordObligationFulfilment,
  type ObligationFulfilment,
  type RecordFulfilmentInput,
} from "@/features/projects/api/obligationsApi";

function obligationsSubtree(projectId: string) {
  return ["projects", projectId, "obligations"] as const;
}

export function useRecordObligationFulfilment(
  projectId: string,
  obligationId: string,
): UseMutationResult<ObligationFulfilment, Error, RecordFulfilmentInput> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: RecordFulfilmentInput) =>
      recordObligationFulfilment(projectId, obligationId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: obligationsSubtree(projectId),
      });
    },
  });
}

export function useClearObligationFulfilment(
  projectId: string,
  obligationId: string,
): UseMutationResult<void, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => clearObligationFulfilment(projectId, obligationId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: obligationsSubtree(projectId),
      });
    },
  });
}
