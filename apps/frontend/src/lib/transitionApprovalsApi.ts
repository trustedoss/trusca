// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Status changes an organization decided one person may not make alone.
 *
 * The flow is two calls on purpose. Asking does not move the finding, and
 * agreeing does; a single call that did both would put the requester's name on
 * a decision nobody else made. The API refuses a decision by the person who
 * asked, so the screen can offer the buttons and let the server be the rule.
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";

export type TransitionApprovalState = "pending" | "approved" | "rejected";

export interface TransitionApprovalOut {
  id: string;
  finding_id: string;
  team_id: string;
  target_status: string;
  justification: string;
  /** Null once the requester's account is gone; the record outlives them. */
  requested_by_user_id: string | null;
  state: TransitionApprovalState;
  decided_by_user_id: string | null;
  decision_note: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface TransitionApprovalListOut {
  items: TransitionApprovalOut[];
  total: number;
}

export interface RequestTransitionBody {
  finding_id: string;
  target_status: string;
  /** The approver has nothing else to judge, so the backend requires 10+ chars. */
  justification: string;
}

export async function requestTransition(
  body: RequestTransitionBody,
): Promise<TransitionApprovalOut> {
  const { data } = await api.post<TransitionApprovalOut>(
    "/v1/transition-approvals",
    body,
  );
  return data;
}

export async function decideTransition(
  approvalId: string,
  body: { approve: boolean; note?: string | null },
): Promise<TransitionApprovalOut> {
  const { data } = await api.post<TransitionApprovalOut>(
    `/v1/transition-approvals/${approvalId}/decision`,
    body,
  );
  return data;
}

/** Requests waiting on a decision, scoped by the server to the caller's teams. */
export async function listPendingTransitions(
  config?: AxiosRequestConfig,
): Promise<TransitionApprovalListOut> {
  const { data } = await api.get<TransitionApprovalListOut>(
    "/v1/transition-approvals",
    config,
  );
  return data;
}

/**
 * The failure tokens the approval endpoints carry as a Problem extension.
 *
 * Read instead of `detail`, which the backend writes in English and always
 * will: rendering it puts English in front of a Korean reader on the screen
 * where the reason matters most. Two of these also share a status code, so the
 * token is the only way to tell "you asked for this" from "you lack the grade".
 */
export const APPROVAL_FAILURE_REASONS = [
  "self_decision",
  "not_team_admin",
  "approval_not_required",
  "already_open",
  "already_decided",
  "not_found",
] as const;

export type ApprovalFailureReason = (typeof APPROVAL_FAILURE_REASONS)[number];

/** The token behind a failed approval call, or null when it carried none. */
export function approvalFailureReason(
  error: unknown,
): ApprovalFailureReason | null {
  const problem = (error as { problem?: Record<string, unknown> } | null)
    ?.problem;
  const reason = problem?.reason;
  return typeof reason === "string" &&
    (APPROVAL_FAILURE_REASONS as readonly string[]).includes(reason)
    ? (reason as ApprovalFailureReason)
    : null;
}
