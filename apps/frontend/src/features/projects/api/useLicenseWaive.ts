/**
 * useLicenseWaive — per-component license waive hooks (Compliance tab).
 *
 * cdxgen sometimes mis-classifies a disjunctive license (e.g. pyphen's
 * ``GPL-2.0-or-later OR MPL-1.1``) as a single forbidden GPL, which then fails
 * the build gate. A team_admin can waive that *single component* with a reason
 * so the gate passes for it alone — every other component keeps the strict
 * classification.
 *
 * Server state lives exclusively in TanStack Query (CLAUDE.md §state). The
 * waive / un-waive mutations:
 *
 *   - seed the team-policy cache with the refreshed {@link LicensePolicyOut}
 *     returned by the backend (no extra round-trip), and
 *   - invalidate the ``["projects", projectId]`` prefix so the Compliance grid,
 *     the Overview risk gauge, AND the gate-result card all re-fetch — the gate
 *     posture for the waived component must flip from blocked → allowed
 *     immediately.
 *   - invalidate the ``["license-policies"]`` prefix so the policy editor (if
 *     open elsewhere) sees the new exception.
 *
 * The team-policy read is exposed via {@link useTeamLicensePolicy}; a 404
 * ("no team policy, static fallback") is a stable answer, not an error, so the
 * caller treats ``data === undefined`` as "no exceptions yet".
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  addTeamLicenseException,
  deleteTeamLicenseException,
  getTeamPolicy,
  type AddLicenseExceptionIn,
  type LicenseException,
  type LicensePolicyOut,
} from "@/lib/licensePoliciesApi";
import { ProblemError } from "@/lib/problem";

export type { LicenseException } from "@/lib/licensePoliciesApi";

export function teamLicensePolicyKey(teamId: string | null) {
  return ["license-policies", "team", teamId ?? "__none__"] as const;
}

/**
 * Do not retry a 4xx — a 404 ("no team policy") and a 403 (read-only) are
 * stable answers. Retry transport (status 0) and 5xx once.
 */
function retryNon4xx(failureCount: number, error: Error): boolean {
  if (
    error instanceof ProblemError &&
    error.status >= 400 &&
    error.status < 500
  ) {
    return false;
  }
  return failureCount < 1;
}

/**
 * Read the team's effective license policy (carries ``license_exceptions``).
 *
 * A 404 means "no team policy — static fallback"; we swallow it to `undefined`
 * so the Compliance tab renders waive affordances without a perpetual error
 * banner. Any non-404 stays as an error the caller can surface if it wants.
 */
export function useTeamLicensePolicy(
  teamId: string | null | undefined,
): UseQueryResult<LicensePolicyOut | null, Error> {
  return useQuery({
    queryKey: teamLicensePolicyKey(teamId ?? null),
    enabled: typeof teamId === "string" && teamId.length > 0,
    staleTime: 30_000,
    retry: retryNon4xx,
    queryFn: async () => {
      try {
        return await getTeamPolicy(teamId as string);
      } catch (error) {
        if (error instanceof ProblemError && error.status === 404) {
          // No team policy yet → no exceptions. Not an error.
          return null;
        }
        throw error;
      }
    },
  });
}

/**
 * Look up whether a (spdx_id, component_purl) pair is already waived in the
 * given policy. Returns the matching {@link LicenseException} or null. Matching
 * is exact on both axes — an org-wide ``component_purl: null`` exception is NOT
 * treated as a per-component waiver here (that is a different, broader waiver
 * the Compliance tab does not own).
 */
export function findComponentException(
  policy: LicensePolicyOut | null | undefined,
  spdxId: string | null,
  componentPurl: string | null,
): LicenseException | null {
  if (!policy || !spdxId || !componentPurl) return null;
  return (
    policy.license_exceptions.find(
      (ex) =>
        ex.spdx_id === spdxId &&
        (ex.component_purl ?? null) === componentPurl,
    ) ?? null
  );
}

interface WaiveVars extends AddLicenseExceptionIn {
  teamId: string;
}

/**
 * Waive one component's forbidden license. Invalidates the project prefix so
 * the gate / overview / compliance surfaces all reflect the new posture.
 */
export function useWaiveLicense(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation<LicensePolicyOut, Error, WaiveVars>({
    mutationFn: ({ teamId, ...payload }) =>
      addTeamLicenseException(teamId, payload),
    onSuccess: (updated) => {
      queryClient.setQueryData(teamLicensePolicyKey(updated.team_id), updated);
      void queryClient.invalidateQueries({ queryKey: ["license-policies"] });
      // Gate-result + overview + compliance all hang off this prefix.
      void queryClient.invalidateQueries({
        queryKey: ["projects", projectId],
      });
    },
  });
}

interface UnwaiveVars {
  teamId: string;
  spdx_id: string;
  component_purl: string;
}

/** Remove one component's waiver. Same invalidation surface as the waive. */
export function useUnwaiveLicense(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation<LicensePolicyOut, Error, UnwaiveVars>({
    mutationFn: ({ teamId, spdx_id, component_purl }) =>
      deleteTeamLicenseException(teamId, { spdx_id, component_purl }),
    onSuccess: (updated) => {
      queryClient.setQueryData(teamLicensePolicyKey(updated.team_id), updated);
      void queryClient.invalidateQueries({ queryKey: ["license-policies"] });
      void queryClient.invalidateQueries({
        queryKey: ["projects", projectId],
      });
    },
  });
}
