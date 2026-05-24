/**
 * License policy REST surface — v2.2 Track C (c3, UI for the c1 API).
 *
 * Thin typed wrapper around the shared `api` axios instance, free of TanStack
 * Query so it can be used in mutations, tests, and imperative code paths.
 *
 * Backend contracts come from:
 *   - apps/backend/api/v1/license_policies.py
 *   - apps/backend/schemas/license_policy.py
 *
 * All wire fields stay snake_case so the OpenAPI contract is the single source
 * of truth. Every 4xx/5xx is `application/problem+json` and surfaces as a
 * {@link ProblemError} via the shared `api` interceptor — the editor maps
 * 422 validation problems to inline / toast copy.
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types — mirror backend schemas/license_policy.py wire shapes (snake_case).
// ---------------------------------------------------------------------------

/** The 3-value gate posture set. Mirrors `PolicyCategory` on the backend. */
export type PolicyCategory = "allowed" | "conditional" | "forbidden";

/** Compound-operator resolution strategy. Mirrors `CompoundStrategy`. */
export type CompoundStrategy = "most_restrictive" | "least_restrictive";

/** Closed operator set for `compound_operator_strategy` keys. */
export type CompoundOperator = "AND" | "OR" | "WITH";

/** One explicit allow-regardless-of-category waiver. */
export interface LicenseException {
  spdx_id: string;
  reason: string;
  /** RFC 3339 datetime or null/absent. */
  expires_at?: string | null;
  /** Scopes the waiver to one component; absent → any component. */
  component_purl?: string | null;
}

/** ORM-derived response shape for a single license policy. */
export interface LicensePolicyOut {
  id: string;
  organization_id: string;
  /** null → org-default scope; set → that team's policy. */
  team_id: string | null;
  name: string | null;
  category_overrides: Record<string, PolicyCategory>;
  license_exceptions: LicenseException[];
  unknown_license_category: PolicyCategory;
  compound_operator_strategy: Record<CompoundOperator, CompoundStrategy>;
  enabled: boolean;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

/** Request body for PUT (upsert) of a team or org policy. */
export interface LicensePolicyUpsertIn {
  name?: string | null;
  category_overrides: Record<string, PolicyCategory>;
  license_exceptions: LicenseException[];
  unknown_license_category: PolicyCategory;
  compound_operator_strategy: Record<CompoundOperator, CompoundStrategy>;
  enabled: boolean;
}

export interface LicensePolicyListPage {
  items: LicensePolicyOut[];
  total: number;
  page: number;
  page_size: number;
}

export interface ListLicensePoliciesParams {
  organization_id?: string | null;
  team_id?: string | null;
  page?: number;
  page_size?: number;
}

// ---------------------------------------------------------------------------
// Defaults — kept in sync with the backend's `_default_compound_strategy()`.
// ---------------------------------------------------------------------------

export function defaultCompoundStrategy(): Record<
  CompoundOperator,
  CompoundStrategy
> {
  return {
    AND: "most_restrictive",
    OR: "least_restrictive",
    WITH: "most_restrictive",
  };
}

/** A blank policy draft used when a scope has no policy yet. */
export function emptyPolicyDraft(): LicensePolicyUpsertIn {
  return {
    name: null,
    category_overrides: {},
    license_exceptions: [],
    unknown_license_category: "conditional",
    compound_operator_strategy: defaultCompoundStrategy(),
    enabled: true,
  };
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export async function listLicensePolicies(
  params: ListLicensePoliciesParams = {},
  config?: AxiosRequestConfig,
): Promise<LicensePolicyListPage> {
  const { data } = await api.get<LicensePolicyListPage>("/v1/license-policies", {
    ...config,
    params: {
      organization_id: params.organization_id ?? undefined,
      team_id: params.team_id ?? undefined,
      page: params.page,
      page_size: params.page_size,
    },
  });
  return data;
}

/**
 * Read the EFFECTIVE policy for a team (team override, else org default).
 *
 * The backend `404`s when neither applies — that means "no policy, falls back
 * to the static catalog", not an error. Callers treat a 404 as "draft a new
 * policy" rather than surfacing it as a failure.
 */
export async function getTeamPolicy(
  teamId: string,
): Promise<LicensePolicyOut> {
  const { data } = await api.get<LicensePolicyOut>(
    `/v1/license-policies/teams/${teamId}`,
  );
  return data;
}

export async function upsertTeamPolicy(
  teamId: string,
  payload: LicensePolicyUpsertIn,
): Promise<LicensePolicyOut> {
  const { data } = await api.put<LicensePolicyOut>(
    `/v1/license-policies/teams/${teamId}`,
    payload,
  );
  return data;
}

export async function deleteTeamPolicy(teamId: string): Promise<void> {
  await api.delete(`/v1/license-policies/teams/${teamId}`);
}

export async function getOrgPolicy(
  organizationId: string,
): Promise<LicensePolicyOut> {
  const { data } = await api.get<LicensePolicyOut>(
    `/v1/license-policies/org/${organizationId}`,
  );
  return data;
}

export async function upsertOrgPolicy(
  organizationId: string,
  payload: LicensePolicyUpsertIn,
): Promise<LicensePolicyOut> {
  const { data } = await api.put<LicensePolicyOut>(
    `/v1/license-policies/org/${organizationId}`,
    payload,
  );
  return data;
}
