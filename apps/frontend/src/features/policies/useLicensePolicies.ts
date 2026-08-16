// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * useLicensePolicies — TanStack Query hooks for the license policy editor.
 *
 * Server state lives exclusively in TanStack Query (no Zustand). Query keys
 * follow the tuple pattern defined in CLAUDE.md:
 *   ["license-policies", { organization_id, team_id, page, page_size }]
 *   ["license-policies", "team", teamId]
 *   ["license-policies", "org", organizationId]
 * Mutations invalidate by the "license-policies" prefix so every variant
 * re-fetches after a save / reset.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  deleteTeamPolicy,
  getOrgPolicy,
  getTeamPolicy,
  listLicensePolicies,
  upsertOrgPolicy,
  upsertTeamPolicy,
  type LicensePolicyListPage,
  type LicensePolicyOut,
  type LicensePolicyUpsertIn,
  type ListLicensePoliciesParams,
} from "@/lib/licensePoliciesApi";
import { ProblemError } from "@/lib/problem";

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export function licensePoliciesQueryKey(params: ListLicensePoliciesParams) {
  return [
    "license-policies",
    {
      organization_id: params.organization_id ?? null,
      team_id: params.team_id ?? null,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    },
  ] as const;
}

export function teamPolicyQueryKey(teamId: string | null) {
  return ["license-policies", "team", teamId ?? "__none__"] as const;
}

export function orgPolicyQueryKey(organizationId: string | null) {
  return ["license-policies", "org", organizationId ?? "__none__"] as const;
}

/**
 * Do not retry a 4xx — a 404 ("no policy, static fallback") and a 403
 * (read-only) are stable answers, not transient failures. Retry transport
 * errors (status 0) and 5xx once.
 */
function retryNon4xx(failureCount: number, error: Error): boolean {
  if (error instanceof ProblemError && error.status >= 400 && error.status < 500) {
    return false;
  }
  return failureCount < 1;
}

// ---------------------------------------------------------------------------
// List query
// ---------------------------------------------------------------------------

export function useLicensePolicies(
  params: ListLicensePoliciesParams,
  options: { enabled?: boolean } = {},
): UseQueryResult<LicensePolicyListPage, Error> {
  return useQuery({
    queryKey: licensePoliciesQueryKey(params),
    queryFn: () => listLicensePolicies(params),
    // Callers that only want the total while a surface is on screen can turn
    // this off. Same escape hatch as `useUnreadCount`, and for the same
    // reason: a hook cannot be called conditionally.
    enabled: options.enabled ?? true,
    staleTime: 30_000,
    retry: retryNon4xx,
  });
}

// ---------------------------------------------------------------------------
// Single-scope queries (drive the editor)
// ---------------------------------------------------------------------------

export function useTeamPolicy(
  teamId: string | null,
): UseQueryResult<LicensePolicyOut, Error> {
  return useQuery({
    queryKey: teamPolicyQueryKey(teamId),
    queryFn: () => getTeamPolicy(teamId!),
    enabled: teamId !== null,
    staleTime: 0,
    retry: retryNon4xx,
  });
}

export function useOrgPolicy(
  organizationId: string | null,
): UseQueryResult<LicensePolicyOut, Error> {
  return useQuery({
    queryKey: orgPolicyQueryKey(organizationId),
    queryFn: () => getOrgPolicy(organizationId!),
    enabled: organizationId !== null,
    staleTime: 0,
    retry: retryNon4xx,
  });
}

// ---------------------------------------------------------------------------
// Mutations — invalidate by the "license-policies" prefix on success.
// ---------------------------------------------------------------------------

interface SaveTeamVars {
  teamId: string;
  payload: LicensePolicyUpsertIn;
}

export function useSaveTeamPolicy() {
  const queryClient = useQueryClient();
  return useMutation<LicensePolicyOut, Error, SaveTeamVars>({
    mutationFn: ({ teamId, payload }) => upsertTeamPolicy(teamId, payload),
    // Error surfaced locally (toast/inline) — keep the global error toast quiet.
    meta: { errorToast: false },
    onSuccess: (updated) => {
      queryClient.setQueryData(teamPolicyQueryKey(updated.team_id), updated);
      void queryClient.invalidateQueries({ queryKey: ["license-policies"] });
    },
  });
}

interface SaveOrgVars {
  organizationId: string;
  payload: LicensePolicyUpsertIn;
}

export function useSaveOrgPolicy() {
  const queryClient = useQueryClient();
  return useMutation<LicensePolicyOut, Error, SaveOrgVars>({
    mutationFn: ({ organizationId, payload }) =>
      upsertOrgPolicy(organizationId, payload),
    meta: { errorToast: false },
    onSuccess: (updated) => {
      queryClient.setQueryData(
        orgPolicyQueryKey(updated.organization_id),
        updated,
      );
      void queryClient.invalidateQueries({ queryKey: ["license-policies"] });
    },
  });
}

export function useResetTeamPolicy() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (teamId) => deleteTeamPolicy(teamId),
    meta: { errorToast: false },
    onSuccess: (_data, teamId) => {
      queryClient.removeQueries({ queryKey: teamPolicyQueryKey(teamId) });
      void queryClient.invalidateQueries({ queryKey: ["license-policies"] });
    },
  });
}
