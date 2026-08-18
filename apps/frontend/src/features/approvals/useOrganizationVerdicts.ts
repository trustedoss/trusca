// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Queries and mutations for organization-wide component rulings.
 *
 * Deciding a ruling invalidates the effective views as well as the list. A
 * ruling that lands without refreshing them leaves every project screen
 * showing the old answer, and the administrator reasonably reads that as the
 * ruling not having worked.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ApprovalStatus } from "@/lib/approvalsApi";
import {
  type EffectiveVerdictOut,
  type OrganizationVerdictListOut,
  type OrganizationVerdictOut,
  getEffectiveVerdict,
  listOrganizationVerdicts,
  openOrganizationVerdict,
  transitionOrganizationVerdict,
} from "@/lib/organizationVerdictsApi";

export function organizationVerdictsQueryKey(
  organizationId: string | null,
  page = 1,
) {
  return ["organization-verdicts", organizationId ?? "__none__", page] as const;
}

export function effectiveVerdictQueryKey(
  projectId: string | null,
  componentId: string | null,
) {
  return [
    "organization-verdicts",
    "effective",
    projectId ?? "__none__",
    componentId ?? "__none__",
  ] as const;
}

export function useOrganizationVerdicts(
  organizationId: string | null,
  page = 1,
) {
  return useQuery<OrganizationVerdictListOut, Error>({
    queryKey: organizationVerdictsQueryKey(organizationId, page),
    enabled: organizationId !== null,
    queryFn: () => listOrganizationVerdicts(organizationId as string, { page }),
  });
}

/**
 * What one project is judged by for one component.
 *
 * Enabled only with both ids, because the drawer mounts before it knows which
 * component it is showing and a request with a placeholder id would 404 and
 * paint an error over a screen that is merely still loading.
 */
export function useEffectiveVerdict(
  projectId: string | null,
  componentId: string | null,
) {
  return useQuery<EffectiveVerdictOut, Error>({
    queryKey: effectiveVerdictQueryKey(projectId, componentId),
    enabled: projectId !== null && componentId !== null,
    queryFn: () =>
      getEffectiveVerdict(projectId as string, componentId as string),
  });
}

export function useOpenOrganizationVerdict(organizationId: string | null) {
  const queryClient = useQueryClient();
  return useMutation<
    OrganizationVerdictOut,
    Error,
    { component_id: string; justification: string }
  >({
    mutationFn: (body) =>
      openOrganizationVerdict(organizationId as string, body),
    onSuccess: () => {
      // Every page, not just the one in view: a new ruling shifts the ordering.
      void queryClient.invalidateQueries({
        queryKey: ["organization-verdicts", organizationId ?? "__none__"],
      });
    },
  });
}

export function useTransitionOrganizationVerdict(organizationId: string | null) {
  const queryClient = useQueryClient();
  return useMutation<
    OrganizationVerdictOut,
    Error,
    { verdictId: string; status: ApprovalStatus; note?: string | null; version: number }
  >({
    mutationFn: ({ verdictId, status, note, version }) =>
      transitionOrganizationVerdict(verdictId, { status, note }, version),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["organization-verdicts", organizationId ?? "__none__"],
      });
      // Every project that was inheriting now inherits something else.
      void queryClient.invalidateQueries({
        queryKey: ["organization-verdicts", "effective"],
      });
    },
  });
}
