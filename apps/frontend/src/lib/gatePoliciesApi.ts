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

export interface GatePolicyOut {
  id: string;
  organization_id: string;
  /** Null for the organization default. */
  team_id: string | null;
  name: string | null;
  epss_threshold: number | null;
  reachable_critical_only: boolean | null;
  malicious_blocks: boolean | null;
  created_at: string;
  updated_at: string;
}

export interface GatePolicyUpsertIn {
  name?: string | null;
  epss_threshold?: number | null;
  reachable_critical_only?: boolean | null;
  malicious_blocks?: boolean | null;
}

/** Where a resolved value came from. `deployment` means no policy decided it. */
export type GatePolicySource = "team" | "organization" | "deployment";

export interface EffectiveGatePolicyOut {
  project_id: string;
  epss_threshold: number | null;
  reachable_critical_only: boolean | null;
  malicious_blocks: boolean | null;
  sources: Record<string, GatePolicySource>;
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
