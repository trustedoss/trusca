/**
 * useApprovals — TanStack Query hooks for the approvals surface.
 *
 * Server state lives exclusively in TanStack Query (no Zustand).
 * Query keys follow the tuple pattern defined in CLAUDE.md:
 *   ["approvals", { ...filter, page, page_size }]
 * Mutations invalidate by the "approvals" prefix so any filter variant
 * re-fetches after a transition or delete.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  deleteApproval,
  getApproval,
  listApprovals,
  transitionApproval,
  type ApprovalAction,
  type ApprovalListPage,
  type ApprovalOut,
  type ListApprovalsParams,
} from "@/lib/approvalsApi";

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export function approvalsQueryKey(params: ListApprovalsParams) {
  return [
    "approvals",
    {
      status: params.status ?? "all",
      team_id: params.team_id ?? null,
      from_dt: params.from_dt ?? null,
      to_dt: params.to_dt ?? null,
      page: params.page ?? 1,
      page_size: params.page_size ?? 25,
    },
  ] as const;
}

export function approvalDetailQueryKey(id: string) {
  return ["approvals", id] as const;
}

// ---------------------------------------------------------------------------
// List query
// ---------------------------------------------------------------------------

export function useApprovals(
  params: ListApprovalsParams,
): UseQueryResult<ApprovalListPage, Error> {
  return useQuery({
    queryKey: approvalsQueryKey(params),
    queryFn: () => listApprovals(params),
    staleTime: 30_000,
  });
}

// ---------------------------------------------------------------------------
// Detail query (used by the drawer to get an ETag before transition)
// ---------------------------------------------------------------------------

export function useApprovalDetail(
  id: string | null,
): UseQueryResult<{ approval: ApprovalOut; etag: string }, Error> {
  return useQuery({
    queryKey: approvalDetailQueryKey(id ?? "__none__"),
    queryFn: () => getApproval(id!),
    enabled: id !== null,
    staleTime: 0, // always fresh — we need the latest ETag before mutating
  });
}

// ---------------------------------------------------------------------------
// Transition mutation
// ---------------------------------------------------------------------------

interface TransitionVars {
  id: string;
  action: ApprovalAction;
  etag: string;
  decisionNote?: string;
}

export function useTransitionApproval() {
  const queryClient = useQueryClient();
  return useMutation<ApprovalOut, Error, TransitionVars>({
    mutationFn: ({ id, action, etag, decisionNote }) =>
      transitionApproval(id, action, etag, decisionNote),
    onSuccess: (updated) => {
      // Invalidate the list (all filter variants) and the individual detail.
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
      // Optimistically update the detail cache with the new data.
      queryClient.setQueryData(approvalDetailQueryKey(updated.id), {
        approval: updated,
        etag: String(updated.version),
      });
    },
  });
}

// ---------------------------------------------------------------------------
// Delete mutation
// ---------------------------------------------------------------------------

export function useDeleteApproval() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id) => deleteApproval(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}
