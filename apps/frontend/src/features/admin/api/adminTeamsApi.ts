/**
 * Admin Teams REST surface — Phase 4 PR #13.
 *
 * Mirrors `apps/backend/schemas/admin.py` (AdminTeam* shapes). All wire fields
 * stay snake_case so the OpenAPI contract is the single source of truth.
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";
import type { TeamMembershipRole } from "@/features/admin/api/adminUsersApi";

export interface AdminTeamListItem {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  member_count: number;
  project_count: number;
  created_at: string;
}

export interface AdminTeamMember {
  user_id: string;
  email: string;
  full_name: string | null;
  role: TeamMembershipRole;
}

export interface AdminTeamDetail {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  project_count: number;
  members: AdminTeamMember[];
  created_at: string;
  updated_at: string;
}

export interface AdminTeamListResponse {
  items: AdminTeamListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminTeamListParams {
  page?: number;
  page_size?: number;
  search?: string | null;
}

export interface AdminTeamCreatePayload {
  name: string;
  slug: string;
  description?: string | null;
}

export interface AdminTeamUpdatePayload {
  name?: string | null;
  slug?: string | null;
  description?: string | null;
}

export interface AdminTeamMemberAddPayload {
  user_id: string;
  role: TeamMembershipRole;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export async function listAdminTeams(
  params: AdminTeamListParams = {},
  config?: AxiosRequestConfig,
): Promise<AdminTeamListResponse> {
  const { data } = await api.get<AdminTeamListResponse>("/v1/admin/teams", {
    ...config,
    params: {
      page: params.page,
      page_size: params.page_size,
      search: params.search ?? undefined,
    },
  });
  return data;
}

export async function getAdminTeam(teamId: string): Promise<AdminTeamDetail> {
  const { data } = await api.get<AdminTeamDetail>(`/v1/admin/teams/${teamId}`);
  return data;
}

export async function createTeam(
  payload: AdminTeamCreatePayload,
): Promise<AdminTeamDetail> {
  const { data } = await api.post<AdminTeamDetail>("/v1/admin/teams", {
    name: payload.name,
    slug: payload.slug,
    description: payload.description ?? null,
  });
  return data;
}

export async function updateTeam(
  teamId: string,
  payload: AdminTeamUpdatePayload,
): Promise<AdminTeamDetail> {
  // Drop nulls so PATCH carries only the fields the caller wants to mutate.
  const body: Record<string, unknown> = {};
  if (payload.name !== undefined && payload.name !== null) body.name = payload.name;
  if (payload.slug !== undefined && payload.slug !== null) body.slug = payload.slug;
  if (payload.description !== undefined) body.description = payload.description;
  const { data } = await api.patch<AdminTeamDetail>(
    `/v1/admin/teams/${teamId}`,
    body,
  );
  return data;
}

export async function deleteTeam(teamId: string): Promise<void> {
  await api.delete(`/v1/admin/teams/${teamId}`);
}

export async function addTeamMember(
  teamId: string,
  payload: AdminTeamMemberAddPayload,
): Promise<AdminTeamDetail> {
  const { data } = await api.post<AdminTeamDetail>(
    `/v1/admin/teams/${teamId}/members`,
    {
      user_id: payload.user_id,
      role: payload.role,
    },
  );
  return data;
}

export async function removeTeamMember(
  teamId: string,
  userId: string,
): Promise<AdminTeamDetail> {
  const { data } = await api.delete<AdminTeamDetail>(
    `/v1/admin/teams/${teamId}/members/${userId}`,
  );
  return data;
}
