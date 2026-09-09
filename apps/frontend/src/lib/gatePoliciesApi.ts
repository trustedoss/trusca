// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Build-gate policy: what stops a build, scoped to an organization or a team.
 *
 * Every field is nullable and null means "not decided at this scope", so the
 * value keeps falling through to the organization and then to the deployment.
 * A form that sends 0 or false where the user meant "leave this alone" would
 * silently pin a decision, which is why the editor distinguishes an empty
 * field from a zero.
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";

/**
 * Statuses an organization may put behind a second person. Mirrors the backend
 * list, and the two are asserted equal in a contract test: a field the editor
 * offers but the API rejects would fail only on save, after the user has done
 * the work.
 */
export const APPROVABLE_STATUSES = [
  "not_affected",
  "false_positive",
  "fixed",
  "suppressed",
] as const;

export type ApprovableStatus = (typeof APPROVABLE_STATUSES)[number];

export interface GatePolicyOut {
  id: string;
  organization_id: string;
  /** Null for the organization default. */
  team_id: string | null;
  name: string | null;
  epss_threshold: number | null;
  reachable_critical_only: boolean | null;
  malicious_blocks: boolean | null;
  /** Null means no transition needs a second person. */
  approval_required_statuses: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface GatePolicyUpsertIn {
  name?: string | null;
  epss_threshold?: number | null;
  reachable_critical_only?: boolean | null;
  malicious_blocks?: boolean | null;
  approval_required_statuses?: string[] | null;
}

/**
 * Display label for one contributing group named in a `GatePolicySource`.
 * `path` is ancestor NAMES, root first, excluding this group's own name.
 */
export interface GatePolicyGroupRef {
  id: string;
  name: string;
  path: string[];
}

/**
 * Where one resolved field's value came from (group-hierarchy Phase 3).
 *
 * `scope` names the KIND of contributor. `group_ids` names WHICH groups,
 * nearest-to-the-project first — 0-1 entries for a fall-through field
 * (`epss_threshold`, `reachable_critical_only`, `malicious_blocks`), 1+ for
 * the `approval_required_statuses` union, where several ancestors at
 * different depths may each have contributed at least one status name.
 * `organization_contributed` answers, for the union field only, whether the
 * organization default ALSO added a status beyond `group_ids`.
 */
export interface GatePolicySource {
  scope: "group" | "organization" | "deployment";
  group_ids: string[];
  group_paths: GatePolicyGroupRef[];
  organization_contributed: boolean;
}

/**
 * The pre-Phase-3 two-tier vocabulary `GatePolicySource.scope` collapses to.
 * `"team"` stands in for `scope: "group"` even when `group_ids` names more
 * than one ancestor — the old vocabulary has no word for "three ancestors
 * deep". Deprecated: kept for one minor version so a caller still reading
 * `sources_legacy` gets an answer that agrees with the structured `sources`
 * rather than drifting from it. Prefer `sources` in new code.
 */
export type GatePolicySourceLegacy =
  | "team"
  | "organization"
  | "team+organization"
  | "deployment";

export interface EffectiveGatePolicyOut {
  project_id: string;
  epss_threshold: number | null;
  reachable_critical_only: boolean | null;
  malicious_blocks: boolean | null;
  /** What this project actually requires a second person for. */
  approval_required_statuses: string[];
  sources: Record<string, GatePolicySource>;
  /** @deprecated Use `sources` — kept for one minor version, see `GatePolicySourceLegacy`. */
  sources_legacy: Record<string, GatePolicySourceLegacy>;
}

/**
 * A team's own row. The backend `404`s when the team has written none, which
 * means "follows the organization" rather than an error: callers render the
 * inherited values and offer to create a row.
 */
export async function getTeamGatePolicy(
  teamId: string,
  config?: AxiosRequestConfig,
): Promise<GatePolicyOut> {
  const { data } = await api.get<GatePolicyOut>(
    `/v1/gate-policies/teams/${teamId}`,
    config,
  );
  return data;
}

export async function upsertTeamGatePolicy(
  teamId: string,
  payload: GatePolicyUpsertIn,
): Promise<GatePolicyOut> {
  const { data } = await api.put<GatePolicyOut>(
    `/v1/gate-policies/teams/${teamId}`,
    payload,
  );
  return data;
}

export async function upsertOrgGatePolicy(
  organizationId: string,
  payload: GatePolicyUpsertIn,
): Promise<GatePolicyOut> {
  const { data } = await api.put<GatePolicyOut>(
    `/v1/gate-policies/org/${organizationId}`,
    payload,
  );
  return data;
}

/** Drop a team's row so it follows its organization again. */
export async function deleteTeamGatePolicy(teamId: string): Promise<void> {
  await api.delete(`/v1/gate-policies/teams/${teamId}`);
}

export interface EpssAvailabilityOut {
  available: boolean;
  refresh_enabled: boolean;
  scored_cves: number;
  last_synced_at: string | null;
}

/**
 * Deployment-scoped: whether anything backs an EPSS threshold at all.
 *
 * The gate result answers the per-scan version of this question. A policy
 * editor cannot ask that one, because it is not looking at a scan.
 */
export async function getEpssAvailability(
  config?: AxiosRequestConfig,
): Promise<EpssAvailabilityOut> {
  const { data } = await api.get<EpssAvailabilityOut>(
    "/v1/gate-policies/epss-availability",
    config,
  );
  return data;
}

export async function getEffectiveGatePolicy(
  projectId: string,
  config?: AxiosRequestConfig,
): Promise<EffectiveGatePolicyOut> {
  const { data } = await api.get<EffectiveGatePolicyOut>(
    `/v1/gate-policies/effective/${projectId}`,
    config,
  );
  return data;
}
