/**
 * Projects + scans REST surface — Phase 2 PR #9 task 2.11.
 *
 * Thin typed wrapper around the existing `api` axios instance. We keep these
 * functions free of TanStack Query so the same calls can be used in mutations,
 * tests, or imperative code paths.
 *
 * Backend contracts come from:
 *   - apps/backend/api/v1/projects.py
 *   - apps/backend/api/v1/scans.py
 *   - apps/backend/schemas/scan.py
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types — mirror the backend schemas/scan.py wire shapes (snake_case).
// ---------------------------------------------------------------------------

export type ProjectVisibility = "team" | "organization";
export type ScanKind = "source" | "container";
export type ScanStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface ProjectPublic {
  id: string;
  team_id: string;
  name: string;
  slug: string;
  description: string | null;
  git_url: string | null;
  default_branch: string | null;
  visibility: ProjectVisibility;
  archived_at: string | null;
  created_by_user_id: string | null;
  latest_scan_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectListResponse {
  items: ProjectPublic[];
  total: number;
  page: number;
  size: number;
}

export interface ScanPublic {
  id: string;
  project_id: string;
  kind: ScanKind;
  status: ScanStatus;
  progress_percent: number;
  current_step: string | null;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  requested_by_user_id: string | null;
  celery_task_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ScanListResponse {
  items: ScanPublic[];
  total: number;
  page: number;
  size: number;
}

export interface ProjectCreatePayload {
  team_id: string;
  name: string;
  slug: string;
  description?: string | null;
  git_url?: string | null;
  default_branch?: string | null;
  visibility?: ProjectVisibility;
}

export interface ProjectUpdatePayload {
  name?: string;
  description?: string | null;
  git_url?: string | null;
  default_branch?: string | null;
  visibility?: ProjectVisibility;
}

export interface ScanTriggerPayload {
  kind?: ScanKind;
  metadata?: Record<string, unknown>;
}

export interface ListProjectsParams {
  team_id?: string;
  include_archived?: boolean;
  q?: string;
  page?: number;
  size?: number;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export async function listProjects(
  params: ListProjectsParams = {},
  config?: AxiosRequestConfig,
): Promise<ProjectListResponse> {
  const { data } = await api.get<ProjectListResponse>("/v1/projects", {
    ...config,
    params: {
      team_id: params.team_id,
      include_archived: params.include_archived,
      q: params.q,
      page: params.page,
      size: params.size,
    },
  });
  return data;
}

export async function createProject(
  payload: ProjectCreatePayload,
): Promise<ProjectPublic> {
  const { data } = await api.post<ProjectPublic>("/v1/projects", payload);
  return data;
}

export async function getProject(projectId: string): Promise<ProjectPublic> {
  const { data } = await api.get<ProjectPublic>(`/v1/projects/${projectId}`);
  return data;
}

export async function updateProject(
  projectId: string,
  payload: ProjectUpdatePayload,
): Promise<ProjectPublic> {
  const { data } = await api.patch<ProjectPublic>(
    `/v1/projects/${projectId}`,
    payload,
  );
  return data;
}

export async function archiveProject(projectId: string): Promise<void> {
  await api.delete(`/v1/projects/${projectId}`);
}

/**
 * Trigger a scan for a project. The backend returns `202 Accepted` with the
 * Scan row already persisted; the WebSocket then streams progress.
 */
export async function triggerScan(
  projectId: string,
  payload: ScanTriggerPayload = {},
): Promise<ScanPublic> {
  const { data } = await api.post<ScanPublic>(
    `/v1/projects/${projectId}/scans`,
    {
      kind: payload.kind ?? "source",
      metadata: payload.metadata ?? {},
    },
  );
  return data;
}

export async function getScan(scanId: string): Promise<ScanPublic> {
  const { data } = await api.get<ScanPublic>(`/v1/scans/${scanId}`);
  return data;
}

export async function listScans(
  projectId: string,
  params: { page?: number; size?: number } = {},
): Promise<ScanListResponse> {
  const { data } = await api.get<ScanListResponse>(
    `/v1/projects/${projectId}/scans`,
    {
      params: { page: params.page, size: params.size },
    },
  );
  return data;
}
