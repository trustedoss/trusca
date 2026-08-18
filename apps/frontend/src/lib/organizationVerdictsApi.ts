// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * An organization ruling on a component once, for every project under it.
 *
 * A component that thirty projects depend on is reviewed thirty times today,
 * and the answer is usually the same because the question is about the
 * component rather than the project. A ruling is where that answer lives.
 *
 * It never overrides a project. Where a project decided for itself that answer
 * stands; the ruling fills the gap where it did not. `scope` on the effective
 * shape says which happened, because a status shown without it sends somebody
 * looking for a project decision that was never made.
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";
import type { ApprovalStatus } from "@/lib/approvalsApi";

/**
 * A ruling as anybody in the organization may read it.
 *
 * The published reason is here because it explains why a component shows as
 * approved in somebody's project. The deliberation around it is not: the
 * server sends `decision_note` and the names of the people involved only to
 * callers who could have written them, so those fields are optional here and
 * absent for everybody else.
 */
export interface OrganizationVerdictOut {
  id: string;
  organization_id: string;
  component_id: string;
  status: ApprovalStatus;
  /** Required on a ruling, unlike a per-project note: it reaches everybody. */
  justification: string;
  decided_at: string | null;
  /** Echoed back as If-Match so two administrators cannot lose a decision. */
  version: number;
  created_at: string;
  updated_at: string;
  /** Present only for a caller who may decide rulings. */
  requested_by_user_id?: string | null;
  decided_by_user_id?: string | null;
  decision_note?: string | null;
}

export interface OrganizationVerdictListOut {
  items: OrganizationVerdictOut[];
  total: number;
  page: number;
  page_size: number;
}

/** Where a project's answer came from. `none` means nobody has decided. */
export type VerdictScope = "project" | "organization" | "none";

export interface EffectiveVerdictOut {
  project_id: string;
  component_id: string;
  status: ApprovalStatus | null;
  scope: VerdictScope;
  /** The organization's reason, when the answer came from there. */
  justification: string | null;
}

export async function listOrganizationVerdicts(
  organizationId: string,
  params?: { page?: number; page_size?: number },
  config?: AxiosRequestConfig,
): Promise<OrganizationVerdictListOut> {
  const { data } = await api.get<OrganizationVerdictListOut>(
    `/v1/organization-verdicts/org/${organizationId}`,
    { ...config, params: { ...params, ...(config?.params ?? {}) } },
  );
  return data;
}

export async function openOrganizationVerdict(
  organizationId: string,
  body: { component_id: string; justification: string },
): Promise<OrganizationVerdictOut> {
  const { data } = await api.post<OrganizationVerdictOut>(
    `/v1/organization-verdicts/org/${organizationId}`,
    body,
  );
  return data;
}

/**
 * Move a ruling along. The version goes out as If-Match, so a decision made
 * from a stale screen is refused with 412 instead of overwriting the other
 * administrator's answer.
 */
export async function transitionOrganizationVerdict(
  verdictId: string,
  body: { status: ApprovalStatus; note?: string | null },
  version: number,
): Promise<OrganizationVerdictOut> {
  const { data } = await api.patch<OrganizationVerdictOut>(
    `/v1/organization-verdicts/${verdictId}`,
    body,
    { headers: { "If-Match": `"${version}"` } },
  );
  return data;
}

export async function getEffectiveVerdict(
  projectId: string,
  componentId: string,
  config?: AxiosRequestConfig,
): Promise<EffectiveVerdictOut> {
  const { data } = await api.get<EffectiveVerdictOut>(
    `/v1/organization-verdicts/effective/${projectId}/${componentId}`,
    config,
  );
  return data;
}
