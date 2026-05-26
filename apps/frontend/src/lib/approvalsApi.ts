/**
 * Approvals REST surface — Phase 4 PR #15.
 *
 * Thin typed wrapper around the shared `api` axios instance. Functions are
 * free of TanStack Query so they can be used in mutations, tests, and
 * imperative code paths.
 *
 * Backend contracts come from:
 *   - apps/backend/api/v1/approvals.py
 *   - apps/backend/schemas/approval.py
 *
 * ETag / If-Match optimistic-concurrency pattern:
 *   - GET /v1/approvals/{id} returns ETag: "{version}" in the response header.
 *   - PATCH /v1/approvals/{id}/transition requires If-Match: "{version}".
 *   - Callers must thread the etag from getApproval() through to
 *     transitionApproval() — the drawer handles this automatically.
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types — mirror backend schemas/approval.py wire shapes (snake_case).
// ---------------------------------------------------------------------------

export type ApprovalStatus =
  | "pending"
  | "under_review"
  | "approved"
  | "rejected";

export type ApprovalAction = "under_review" | "approved" | "rejected";

export interface ApprovalOut {
  id: string;
  component_id: string;
  project_id: string;
  team_id: string;
  requested_by_user_id: string | null;
  requested_at: string; // ISO datetime
  status: ApprovalStatus;
  decided_by_user_id: string | null;
  decided_at: string | null;
  decision_note: string | null;
  version: number; // ETag value
  /**
   * P1 #6 — denormalised display fields surfaced on every approval row by
   * the list endpoint (`GET /v1/approvals`) via batched IN(...) lookups, so
   * the Approvals queue UI can render real labels in the Component / Project
   * columns instead of UUID prefixes. Nullable because the single-row
   * endpoints (POST / PATCH / GET-by-id) don't carry the lookups yet, and
   * because the referent could in theory be hard-deleted from underneath.
   */
  component_name: string | null;
  component_purl: string | null;
  project_name: string | null;
  project_slug: string | null;
}

export interface ApprovalListPage {
  items: ApprovalOut[];
  total: number;
  page: number;
  page_size: number;
}

export interface ListApprovalsParams {
  status?: ApprovalStatus | "all" | null;
  team_id?: string | null;
  from_dt?: string | null;
  to_dt?: string | null;
  page?: number;
  page_size?: number;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export async function listApprovals(
  params: ListApprovalsParams = {},
  config?: AxiosRequestConfig,
): Promise<ApprovalListPage> {
  const { data } = await api.get<ApprovalListPage>("/v1/approvals", {
    ...config,
    params: {
      // "all" is a UI sentinel — do not forward it to the backend.
      status:
        params.status && params.status !== "all" ? params.status : undefined,
      team_id: params.team_id ?? undefined,
      from_dt: params.from_dt ?? undefined,
      to_dt: params.to_dt ?? undefined,
      page: params.page,
      page_size: params.page_size,
    },
  });
  return data;
}

/**
 * Fetch a single approval and extract the ETag header.
 *
 * The backend returns `ETag: "{version}"`. We strip the surrounding
 * quotes to get the raw numeric string, then pass it back in If-Match.
 */
export async function getApproval(
  id: string,
): Promise<{ approval: ApprovalOut; etag: string }> {
  const response = await api.get<ApprovalOut>(`/v1/approvals/${id}`, {
    // Tell axios to expose ETag in the response headers.
    // axios exposes all lower-cased header names.
  });
  const rawEtag = (response.headers["etag"] as string | undefined) ?? "";
  // Strip optional surrounding double-quotes from the ETag value.
  const etag = rawEtag.replace(/^"|"$/g, "");
  return { approval: response.data, etag };
}

export async function createApproval(data: {
  component_id: string;
  project_id: string;
}): Promise<ApprovalOut> {
  const { data: approval } = await api.post<ApprovalOut>("/v1/approvals", data);
  return approval;
}

/**
 * Transition an approval to a new status.
 *
 * @param id        Approval UUID
 * @param action    Target state: "under_review" | "approved" | "rejected"
 * @param etag      Version tag received from getApproval (without quotes)
 * @param decisionNote Optional free-text note for approve / reject actions.
 */
export async function transitionApproval(
  id: string,
  action: ApprovalAction,
  etag: string,
  decisionNote?: string,
): Promise<ApprovalOut> {
  const { data } = await api.patch<ApprovalOut>(
    `/v1/approvals/${id}/transition`,
    {
      action,
      decision_note: decisionNote ?? null,
    },
    {
      headers: {
        // Re-wrap in quotes as the HTTP spec mandates for If-Match.
        "If-Match": `"${etag}"`,
      },
    },
  );
  return data;
}

export async function deleteApproval(id: string): Promise<void> {
  await api.delete(`/v1/approvals/${id}`);
}
