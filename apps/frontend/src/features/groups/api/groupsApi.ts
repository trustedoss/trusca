// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Groups REST surface, group-hierarchy Phase 4 PR 4-B.
 *
 * Thin typed wrapper around the shared `api` axios instance, mirroring the
 * `lib/projectsApi.ts` pattern (functions free of TanStack Query so the same
 * calls compose into hooks, tests, or imperative code paths).
 *
 * Backend contracts:
 *   - apps/backend/api/v1/groups.py
 *   - apps/backend/schemas/group.py
 *
 * The three read endpoints this PR consumes are viewer-role (list/detail) or
 * developer-role (members); see that router's own docstring for why members
 * is gated a grade above the other two. A 404 from detail/members is the
 * backend's existence-hide response (nonexistent id and inaccessible-but-real
 * id render identically); callers distinguish that from other failures via
 * `ProblemError.status === 404`.
 */
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types, mirroring apps/backend/schemas/group.py wire shapes (snake_case).
// ---------------------------------------------------------------------------

/** Shared narrow ancestor/self shape, id/name/slug only (schemas.group.GroupBreadcrumbEntry). */
export interface GroupBreadcrumbEntry {
  id: string;
  name: string;
  slug: string;
}

/** Row in `GET /v1/groups`, either a flat search hit or a drill-down child. */
export interface GroupListItem {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  parent_group_id: string | null;
  /** Direct child groups only (not the whole subtree). */
  child_group_count: number;
  /** Projects owned directly by this group (not the whole subtree). */
  project_count: number;
  /** Direct memberships on this group (not inherited). */
  member_count: number;
  updated_at: string;
}

export interface GroupListPage {
  items: GroupListItem[];
  total: number;
  page: number;
  page_size: number;
}

/** 30-day activity summary, ALWAYS a subtree aggregate (schemas.group.GroupSummaryStats). */
export interface GroupSummaryStats {
  window_days: number;
  subtree_scan_count: number;
  subtree_approvals_processed_count: number;
  subtree_new_member_count: number;
}

export interface GroupDetail {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  parent_group_id: string | null;
  /** Root-first chain of ancestors, NOT including this group itself. */
  ancestors: GroupBreadcrumbEntry[];
  child_group_count: number;
  project_count: number;
  member_count: number;
  stats: GroupSummaryStats;
  created_at: string;
  updated_at: string;
}

export interface GroupMemberEntry {
  user_id: string;
  email: string;
  full_name: string | null;
  role: string;
  is_service_account: boolean;
}

/** A membership the group's cascade grants access through, from an ancestor. */
export interface GroupInheritedMemberEntry {
  user_id: string;
  email: string;
  full_name: string | null;
  role: string;
  is_service_account: boolean;
  /** The ancestor group this membership actually lives on. */
  source_group_id: string;
  /** Display name of source_group_id, for attributing the row. */
  source_group_name: string;
}

export interface GroupMembersResponse {
  direct: GroupMemberEntry[];
  inherited: GroupInheritedMemberEntry[];
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export interface ListGroupsParams {
  /** Flat, whole-tree name search. When set, `parent_id` is ignored. */
  q?: string;
  /** Drill-down mode (only consulted when `q` is unset). Omitted = root groups. */
  parent_id?: string;
  page?: number;
  page_size?: number;
}

export async function listGroups(
  params: ListGroupsParams = {},
): Promise<GroupListPage> {
  const { data } = await api.get<GroupListPage>("/v1/groups", {
    params: {
      q: params.q,
      parent_id: params.parent_id,
      page: params.page,
      page_size: params.page_size,
    },
  });
  return data;
}

export async function getGroup(groupId: string): Promise<GroupDetail> {
  const { data } = await api.get<GroupDetail>(`/v1/groups/${groupId}`);
  return data;
}

export async function getGroupMembers(
  groupId: string,
): Promise<GroupMembersResponse> {
  const { data } = await api.get<GroupMembersResponse>(
    `/v1/groups/${groupId}/members`,
  );
  return data;
}
