/**
 * Source-archive upload surface — feat/zip-upload.
 *
 * Wraps `POST /v1/projects/{project_id}/source-archive` (multipart, field
 * name `upload`). We go through the shared `api` axios instance so the upload
 * inherits:
 *   - the `Authorization: Bearer …` request interceptor,
 *   - the single-flight 401 refresh,
 *   - the RFC 7807 → {@link ProblemError} response mapping.
 *
 * axios exposes `onUploadProgress`, so we get a real progress signal for the
 * (potentially 100 MiB) body without hand-rolling XHR. The backend caps the
 * body at `SOURCE_ARCHIVE_MAX_BYTES` (100 MiB) and per-project quota at
 * 500 MiB; the matching client-side guards live in `lib/zipFolder.ts` and the
 * upload UI so we can fail fast before sending the bytes.
 *
 * Backend contract (apps/backend/api/v1/projects.py):
 *   - 201 { "archive_id": "<uuid>" }
 *   - 413 too large (also pre-flight Content-Length)
 *   - 415 wrong extension / MIME / magic bytes
 *   - 507 per-project quota exceeded
 *   - 400 malformed request
 *   - 404 other team's project (existence-hide)
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";
import { ProblemError } from "@/lib/problem";

export interface SourceArchiveUploadResponse {
  archive_id: string;
}

export interface UploadSourceArchiveOptions {
  /** 0–100 progress callback wired from axios `onUploadProgress`. */
  onProgress?: (percent: number) => void;
  /** Abort signal so the UI can cancel an in-flight upload. */
  signal?: AbortSignal;
}

/**
 * Upload a `.zip` archive for a project and return the persisted archive id.
 *
 * The `file` is appended under the `upload` field exactly as the backend
 * expects. We deliberately do NOT set a `Content-Type` header — the browser
 * sets `multipart/form-data; boundary=…` itself, and overriding it would drop
 * the boundary and break the multipart parse on the server.
 */
export async function uploadSourceArchive(
  projectId: string,
  file: File | Blob,
  options: UploadSourceArchiveOptions = {},
): Promise<SourceArchiveUploadResponse> {
  const form = new FormData();
  // Preserve a filename so the backend's extension check (`.zip`) is satisfied
  // even when the caller passes a generated Blob from the folder-zip path.
  const filename =
    file instanceof File && file.name ? file.name : "source-archive.zip";
  form.append("upload", file, filename);

  const config: AxiosRequestConfig = {
    signal: options.signal,
    onUploadProgress: (event) => {
      if (!options.onProgress) return;
      // `total` is unknown for streamed bodies in some browsers; fall back to
      // the file size we already know so the bar never stalls at 0.
      const total = event.total ?? (file as { size?: number }).size ?? 0;
      if (total > 0) {
        const percent = Math.min(
          100,
          Math.round((event.loaded / total) * 100),
        );
        options.onProgress(percent);
      }
    },
  };

  const { data } = await api.post<SourceArchiveUploadResponse>(
    `/v1/projects/${projectId}/source-archive`,
    form,
    config,
  );
  return data;
}

// ---------------------------------------------------------------------------
// Error → i18n key mapping (mirrors features/admin/lib/adminErrorMessage.ts).
// ---------------------------------------------------------------------------

/**
 * The user-facing error namespace key for a failed upload. Keyed by HTTP
 * status so the copy matches the RFC 7807 problem the backend returns. The
 * returned key lives under `scans:upload.errors.*`.
 */
export function uploadErrorMessageKey(err: unknown): string {
  if (err instanceof ProblemError) {
    switch (err.status) {
      case 413:
        return "upload.errors.too_large";
      case 415:
        return "upload.errors.not_a_zip";
      case 507:
        return "upload.errors.quota_exceeded";
      case 404:
        return "upload.errors.not_found";
      case 400:
        return "upload.errors.bad_request";
      case 0:
        return "upload.errors.network";
      default:
        return "upload.errors.unknown";
    }
  }
  return "upload.errors.unknown";
}

/**
 * The locale-independent token for the failure — used by test ids / e2e so a
 * suite can assert on the specific failure mode without depending on the
 * translated copy.
 */
export function uploadErrorToken(err: unknown): string {
  if (err instanceof ProblemError) {
    switch (err.status) {
      case 413:
        return "too_large";
      case 415:
        return "not_a_zip";
      case 507:
        return "quota_exceeded";
      case 404:
        return "not_found";
      case 400:
        return "bad_request";
      case 0:
        return "network";
      default:
        return "unknown";
    }
  }
  return "unknown";
}
