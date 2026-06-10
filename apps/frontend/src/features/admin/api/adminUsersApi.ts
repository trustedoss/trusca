/**
 * Admin Users REST surface — Phase 4 PR #13.
 *
 * Mirrors `apps/backend/schemas/admin.py` 1:1 (snake_case wire, camelCase only
 * inside the call-site contract objects). Every function returns the parsed
 * Pydantic shape; errors propagate as `ProblemError` from the shared axios
 * interceptor.
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";

export type UserRole = "super_admin" | "team_admin" | "developer";
export type TeamMembershipRole = "team_admin" | "developer";

export interface TeamMembershipPublic {
  team_id: string;
  team_name: string;
  role: TeamMembershipRole;
}

export interface AdminUserListItem {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  is_superuser: boolean;
  /**
   * H-2: membership rollup computed by the list endpoint — highest-effective
   * role + membership count. Optional because the detail response (which
   * extends this shape) carries full `memberships` instead.
   */
  role?: UserRole;
  team_count?: number;
  last_login_at: string | null;
  created_at: string;
}

export interface AdminUserDetail extends AdminUserListItem {
  updated_at: string;
  scan_count: number;
  memberships: TeamMembershipPublic[];
}

export interface AdminUserListResponse {
  items: AdminUserListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminUserListParams {
  page?: number;
  page_size?: number;
  /**
   * Filter by canonical role. The backend matches "super_admin" against
   * `is_superuser=true`, while "team_admin" / "developer" filter on the
   * highest-priority membership role.
   */
  role?: UserRole | null;
  /** True → active only, false → inactive only, null/undefined → all. */
  active?: boolean | null;
  /** Substring search across email + full_name. */
  search?: string | null;
}

export interface RoleUpdatePayload {
  role: UserRole;
  /** Required when role is team_admin or developer; ignored for super_admin. */
  team_id?: string | null;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export async function listAdminUsers(
  params: AdminUserListParams = {},
  config?: AxiosRequestConfig,
): Promise<AdminUserListResponse> {
  const { data } = await api.get<AdminUserListResponse>("/v1/admin/users", {
    ...config,
    params: {
      page: params.page,
      page_size: params.page_size,
      role: params.role ?? undefined,
      active: params.active ?? undefined,
      search: params.search ?? undefined,
    },
  });
  return data;
}

export async function getAdminUser(userId: string): Promise<AdminUserDetail> {
  const { data } = await api.get<AdminUserDetail>(`/v1/admin/users/${userId}`);
  return data;
}

export async function updateUserRole(
  userId: string,
  payload: RoleUpdatePayload,
): Promise<AdminUserDetail> {
  const { data } = await api.patch<AdminUserDetail>(
    `/v1/admin/users/${userId}/role`,
    {
      role: payload.role,
      team_id: payload.team_id ?? null,
    },
  );
  return data;
}

export async function deactivateUser(userId: string): Promise<AdminUserDetail> {
  const { data } = await api.patch<AdminUserDetail>(
    `/v1/admin/users/${userId}/deactivate`,
  );
  return data;
}

export async function activateUser(userId: string): Promise<AdminUserDetail> {
  const { data } = await api.patch<AdminUserDetail>(
    `/v1/admin/users/${userId}/activate`,
  );
  return data;
}

/**
 * Issues a one-shot password-reset token. Backend returns 204 — we surface
 * void so callers don't accidentally read the empty body.
 */
export async function requestPasswordReset(userId: string): Promise<void> {
  await api.post(`/v1/admin/users/${userId}/password-reset`);
}
