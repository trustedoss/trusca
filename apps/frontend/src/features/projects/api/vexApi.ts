/**
 * VEX export + import wire surface — v2.1 Track A (A3 UI over A1/A2 backend).
 *
 * Two endpoints back the VEX UI on the project Vulnerabilities tab:
 *
 *   - GET  /v1/projects/{id}/vex?format=openvex|cyclonedx  → document download
 *   - POST /v1/projects/{id}/vex/import (multipart file)   → import summary
 *
 * Export streams the document as a Blob through the shared axios `api` instance
 * (bearer token rides the `Authorization` header, NOT the URL/history), exactly
 * like `downloadSbom` / the vulnerability PDF report. Import posts a multipart
 * file and returns the JSON summary `{format, matched, applied, skipped,
 * errors[]}`.
 *
 * Hard rules (CLAUDE.md):
 *   - All 4xx/5xx responses are `application/problem+json` and surface as
 *     {@link ProblemError} via the shared `api` interceptor. The 403 (not
 *     team_admin), 404 (project hidden), 413 (too large), and 422 (malformed)
 *     cases all arrive as ProblemError so call sites have one error type.
 */
import { api } from "@/lib/api";
import {
  parseContentDispositionFilename,
  safeFilenameToken,
} from "@/lib/download";

export type VexFormat = "openvex" | "cyclonedx";

export interface VexExportDownload {
  blob: Blob;
  filename: string;
}

/**
 * Fetch a VEX document for a project as a Blob and resolve the suggested
 * download filename. The backend sets a
 * `Content-Disposition: attachment; filename="..."` header; we fall back to a
 * client-built name (`<project>-vex-<format>.json`) when it is absent.
 */
export async function downloadVex(
  projectId: string,
  format: VexFormat,
  projectName?: string | null,
): Promise<VexExportDownload> {
  const response = await api.get<Blob>(`/v1/projects/${projectId}/vex`, {
    params: { format },
    responseType: "blob",
  });
  const headers = (response.headers ?? {}) as Record<string, string>;
  const disposition =
    headers["content-disposition"] ?? headers["Content-Disposition"] ?? "";
  const headerFilename = parseContentDispositionFilename(disposition);
  const fallback = `${safeFilenameToken(
    projectName ?? projectId,
  )}-vex-${format}.json`;
  // The backend emits OpenVEX/CycloneDX JSON; force a JSON Blob type so the
  // download is labeled correctly even if the transport handed us an untyped
  // Blob.
  const blob = new Blob([response.data as Blob], {
    type: "application/json",
  });
  return { blob, filename: headerFilename ?? fallback };
}

// ---------------------------------------------------------------------------
// Import — mirror apps/backend/schemas/vex_import.py
// ---------------------------------------------------------------------------

export type VexImportSkipReason =
  | "unknown_vulnerability"
  | "unknown_component"
  | "ambiguous_match"
  | "unmapped_status"
  | "illegal_transition"
  | "already_at_target"
  | "forbidden_transition"
  | "malformed_statement";

export interface VexImportItemError {
  vulnerability: string | null;
  product: string | null;
  reason: VexImportSkipReason;
  detail: string;
}

export interface VexImportSummary {
  format: VexFormat;
  matched: number;
  applied: number;
  skipped: number;
  errors: VexImportItemError[];
}

/**
 * Upload a VEX document (OpenVEX or CycloneDX VEX, format auto-detected) and
 * return the import summary. The file rides a multipart body under the `upload`
 * field, matching the backend `UploadFile = File(...)` parameter name.
 *
 * Errors (403/404/413/422) surface as {@link ProblemError} via the shared
 * interceptor; the caller renders `error.detail` and branches on
 * `error.status` for an actionable message.
 */
export async function importVex(
  projectId: string,
  file: File,
): Promise<VexImportSummary> {
  const form = new FormData();
  form.append("upload", file);
  const { data } = await api.post<VexImportSummary>(
    `/v1/projects/${projectId}/vex/import`,
    form,
  );
  return data;
}
