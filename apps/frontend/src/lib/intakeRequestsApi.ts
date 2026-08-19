// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Asking to use a package before anything has scanned it.
 *
 * Every route here answers 404 when the deployment has not turned the queue
 * on, which is the default. Callers should not reach them at all in that case:
 * `useDeploymentFeatures()` says whether the surface exists, and the shell
 * draws no entry point when it does not.
 */
import { api } from "@/lib/api";
import type { ApprovalStatus } from "@/lib/approvalsApi";

export interface IntakeRequestOut {
  id: string;
  project_id: string;
  team_id: string;
  /** The package, as a purl: what a later scan matches the answer against. */
  purl: string;
  justification: string;
  status: ApprovalStatus;
  requested_by_user_id: string | null;
  decided_by_user_id: string | null;
  decision_note: string | null;
  decided_at: string | null;
  /** Echoed back as If-Match so two reviewers cannot lose a decision. */
  version: number;
  created_at: string;
  updated_at: string;
}

export interface IntakeRequestListOut {
  items: IntakeRequestOut[];
  total: number;
}

export async function listIntakeRequests(params?: {
  project_id?: string;
}): Promise<IntakeRequestListOut> {
  const { data } = await api.get<IntakeRequestListOut>("/v1/intake-requests", {
    params,
  });
  return data;
}

export async function openIntakeRequest(body: {
  project_id: string;
  purl: string;
  justification: string;
}): Promise<IntakeRequestOut> {
  const { data } = await api.post<IntakeRequestOut>("/v1/intake-requests", body);
  return data;
}

export async function transitionIntakeRequest(
  requestId: string,
  body: { status: ApprovalStatus; note?: string | null },
  version: number,
): Promise<IntakeRequestOut> {
  const { data } = await api.patch<IntakeRequestOut>(
    `/v1/intake-requests/${requestId}`,
    body,
    { headers: { "If-Match": `"${version}"` } },
  );
  return data;
}
