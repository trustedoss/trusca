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
 * Unarchive a previously archived project. Backed by `PATCH /v1/projects/{id}`
 * with `archived: false`; if the backend rejects the field the call surfaces
 * as a ProblemError to the caller.
 */
export async function unarchiveProject(
  projectId: string,
): Promise<ProjectPublic> {
  const { data } = await api.patch<ProjectPublic>(
    `/v1/projects/${projectId}`,
    { archived: false },
  );
  return data;
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

/**
 * Cancel a queued/running scan owned by the current user's team (PR-A1).
 *
 * Backend contract — `POST /v1/scans/{scan_id}/cancel`:
 *   - `developer` role, own-team scans only (other teams 404 — existence-hide).
 *   - Already-terminal scans (succeeded/failed/cancelled) → 409 with the
 *     `scan_already_cancelled` Problem extension.
 *   - Unknown / cross-team scan id → 404 with `scan_not_found`.
 * On success the cancelled `ScanPublic` row is returned (status `cancelled`).
 * The WebSocket then streams a terminal `cancelled` frame on its own; this
 * call only flips the persisted row.
 */
export async function cancelScan(scanId: string): Promise<ScanPublic> {
  const { data } = await api.post<ScanPublic>(`/v1/scans/${scanId}/cancel`);
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

export interface ListMyScansParams {
  status?: ScanStatus;
  page?: number;
  size?: number;
}

/**
 * Cross-project scan queue for the current user. Backed by
 * `GET /v1/scans` with optional `status` filter and standard pagination.
 * Used by the global ScansPage in the sidebar — mirrors AdminScansPage but
 * is scoped to the user's reachable teams.
 */
export async function listMyScans(
  params: ListMyScansParams = {},
): Promise<ScanListResponse> {
  const { data } = await api.get<ScanListResponse>("/v1/scans", {
    params: {
      status: params.status,
      page: params.page,
      size: params.size,
    },
  });
  return data;
}

// ---------------------------------------------------------------------------
// SBOM export — Phase 3 / Step 4-A.
// ---------------------------------------------------------------------------

export type SbomFormat =
  | "cyclonedx-json"
  | "cyclonedx-xml"
  | "spdx-json"
  | "spdx-tv";

export interface SbomDownload {
  blob: Blob;
  filename: string;
  format: SbomFormat;
}

const SBOM_FALLBACK_EXTENSIONS: Record<SbomFormat, string> = {
  "cyclonedx-json": "cdx.json",
  "cyclonedx-xml": "cdx.xml",
  "spdx-json": "spdx.json",
  "spdx-tv": "spdx",
};

/**
 * Fetch the SBOM document as a Blob and parse the suggested filename out of
 * the `Content-Disposition` header. The caller wires the blob into a
 * `URL.createObjectURL` + `<a download>` click trigger; doing the request
 * through axios (rather than `window.location =`) keeps the bearer token
 * out of the URL / browser history / any reverse-proxy access logs.
 */
export async function downloadSbom(
  projectId: string,
  format: SbomFormat,
): Promise<SbomDownload> {
  const response = await api.get<Blob>(`/v1/projects/${projectId}/sbom`, {
    params: { format },
    responseType: "blob",
  });
  const headers = (response.headers ?? {}) as Record<string, string>;
  const disposition =
    headers["content-disposition"] ?? headers["Content-Disposition"] ?? "";
  // RFC 6266 filename — handles `filename="x.json"` and bare `filename=x.json`.
  const match = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/i);
  const filename =
    match?.[1] ?? `sbom-${projectId}.${SBOM_FALLBACK_EXTENSIONS[format]}`;
  return { blob: response.data as Blob, filename, format };
}
