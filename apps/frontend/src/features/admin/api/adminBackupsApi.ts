/**
 * Admin Backups REST surface — Phase 6 PR #19 chore D.
 *
 * Mirrors the contract pinned in `apps/backend/api/v1/admin_backup.py`.
 * All endpoints require a JWT bearer token and super-admin (the backend
 * applies the existence-hide guard so non-super-admins receive 404s, which
 * propagate up as a `ProblemError` here).
 *
 * Wire shape (snake_case mirrors the backend Pydantic models):
 *   GET    /v1/admin/backup                    → BackupListResponse
 *   POST   /v1/admin/backup                    → 202 BackupTriggerResponse
 *   GET    /v1/admin/backup/{name}/download    → tar.gz stream
 *   POST   /v1/admin/backup/restore            → 202 BackupRestoreResponse
 *   DELETE /v1/admin/backup/{name}             → 204
 *
 * Download flow: we fetch through axios with `responseType: "blob"` so the
 * bearer token stays in the Authorization header (out of URL/history). The
 * resulting blob is streamed to disk via `URL.createObjectURL` + a synthetic
 * `<a download>` click; the same pattern the audit CSV export uses.
 *
 * Restore flow: multipart upload with the literal `X-Confirm-Restore: yes`
 * header — the backend rejects the call without this header to make
 * accidental destructive uploads impossible.
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";

export type BackupKind = "auto" | "manual";

export interface BackupInfo {
  name: string;
  kind: BackupKind;
  created_at: string;
  size_bytes: number;
  db_revision: string | null;
}

export interface BackupListResponse {
  items: BackupInfo[];
  total: number;
}

export interface BackupTriggerResponse {
  task_id: string;
  name: string;
}

export interface BackupRestoreResponse {
  task_id: string;
  message: string;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export async function listBackups(
  config?: AxiosRequestConfig,
): Promise<BackupListResponse> {
  const { data } = await api.get<BackupListResponse>("/v1/admin/backup", config);
  return data;
}

export async function triggerManualBackup(): Promise<BackupTriggerResponse> {
  const { data } = await api.post<BackupTriggerResponse>("/v1/admin/backup");
  return data;
}

/**
 * Stream the tar.gz down through axios as a blob, then trigger the browser
 * download dialog via a synthetic `<a download>` click. The bearer token
 * stays inside the Authorization header (never in the URL), and the same
 * `URL.createObjectURL` + revoke pattern the audit CSV export uses keeps
 * memory cleanup deterministic.
 */
export async function downloadBackup(name: string): Promise<void> {
  const response = await api.get<Blob>(
    `/v1/admin/backup/${encodeURIComponent(name)}/download`,
    { responseType: "blob" },
  );
  // Honour the Content-Disposition filename when the backend supplies one;
  // otherwise default to `<name>.tar.gz` so the file is unambiguous on disk.
  const disposition =
    (response.headers as Record<string, string>)["content-disposition"] ?? "";
  const match = disposition.match(/filename="?([^";]+)"?/);
  const filename = match?.[1] ?? `${name}.tar.gz`;
  const blobUrl = URL.createObjectURL(response.data as Blob);
  const anchor = document.createElement("a");
  anchor.href = blobUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Free the blob URL after a tick so the browser has time to start the
  // download before we revoke. 4s matches the audit CSV pattern.
  setTimeout(() => URL.revokeObjectURL(blobUrl), 4000);
}

export async function deleteBackup(name: string): Promise<void> {
  await api.delete(`/v1/admin/backup/${encodeURIComponent(name)}`);
}

/**
 * Multipart upload of a previously-downloaded tar.gz. The backend requires
 * the `X-Confirm-Restore: yes` header to be present — we set it
 * unconditionally because the UI gates the call behind a typing
 * confirmation strip (the user must type "restore" to enable the button).
 */
export async function uploadRestore(
  file: File,
): Promise<BackupRestoreResponse> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post<BackupRestoreResponse>(
    "/v1/admin/backup/restore",
    form,
    {
      headers: {
        "X-Confirm-Restore": "yes",
        // Let axios set the multipart boundary by passing FormData directly.
        "Content-Type": "multipart/form-data",
      },
    },
  );
  return data;
}
