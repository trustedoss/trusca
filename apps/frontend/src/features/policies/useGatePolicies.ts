// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Queries and mutations for build-gate policy.
 *
 * A 404 from the team endpoint is an answer, not a failure: it means the team
 * has written no row and follows its organization. Treating it as an error
 * would put a red state on a screen whose correct reading is "nothing to
 * override here yet", so it is surfaced as absence and never retried.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ProblemError } from "@/lib/problem";
import {
  type EffectiveGatePolicyOut,
  type GatePolicyOut,
  type GatePolicyUpsertIn,
  deleteTeamGatePolicy,
  getEffectiveGatePolicy,
  getTeamGatePolicy,
  upsertOrgGatePolicy,
  upsertTeamGatePolicy,
} from "@/lib/gatePoliciesApi";

export function teamGatePolicyQueryKey(teamId: string | null) {
  return ["gate-policies", "team", teamId ?? "__none__"] as const;
}

export function effectiveGatePolicyQueryKey(projectId: string | null) {
  return ["gate-policies", "effective", projectId ?? "__none__"] as const;
}

/** A 4xx is a stable answer here; only transport and 5xx are worth retrying. */
function retryNon4xx(failureCount: number, error: Error): boolean {
  if (error instanceof ProblemError && error.status >= 400 && error.status < 500) {
    return false;
  }
  return failureCount < 1;
}

/**
 * The team's own row, or `null` when it has none.
 *
 * The null is the useful part: the editor renders inherited values and offers
 * to create an override, rather than showing an empty form that looks like the
 * team has switched everything off.
 */
export function useTeamGatePolicy(teamId: string | null) {
  return useQuery<GatePolicyOut | null, Error>({
    queryKey: teamGatePolicyQueryKey(teamId),
    enabled: teamId !== null,
    retry: retryNon4xx,
    queryFn: async () => {
      try {
        return await getTeamGatePolicy(teamId as string);
      } catch (error) {
        if (error instanceof ProblemError && error.status === 404) {
          return null;
        }
        throw error;
      }
    },
  });
}

export function useEffectiveGatePolicy(projectId: string | null) {
  return useQuery<EffectiveGatePolicyOut, Error>({
    queryKey: effectiveGatePolicyQueryKey(projectId),
    enabled: projectId !== null,
    retry: retryNon4xx,
    queryFn: () => getEffectiveGatePolicy(projectId as string),
  });
}

export function useUpsertTeamGatePolicy(teamId: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: GatePolicyUpsertIn) =>
      upsertTeamGatePolicy(teamId as string, payload),
    onSuccess: () => {
      // Both keys: the row changed, and so did what any project under this
      // team is judged by. Leaving the effective view stale would show the
      // editor's new value beside an old verdict.
      void client.invalidateQueries({ queryKey: teamGatePolicyQueryKey(teamId) });
      void client.invalidateQueries({ queryKey: ["gate-policies", "effective"] });
    },
  });
}

export function useUpsertOrgGatePolicy(organizationId: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: GatePolicyUpsertIn) =>
      upsertOrgGatePolicy(organizationId as string, payload),
    onSuccess: () => {
      // An organization change reaches every team that did not override the
      // field, so nothing under this prefix can be assumed still current.
      void client.invalidateQueries({ queryKey: ["gate-policies"] });
    },
  });
}

export function useDeleteTeamGatePolicy(teamId: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => deleteTeamGatePolicy(teamId as string),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["gate-policies"] });
    },
  });
}
