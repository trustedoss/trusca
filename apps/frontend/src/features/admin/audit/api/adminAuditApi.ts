/**
 * Admin Audit-Log REST surface — Phase 4 PR #14 §4.7.
 *
 * Mirrors `apps/backend/schemas/admin_ops.py` `AuditSearchQuery` + `AuditLogListPage`.
 *
 *   GET /v1/admin/audit              → AuditLogListPage   (paginated JSON)
 *   GET /v1/admin/audit/export.csv   → text/csv           (streaming download)
 *
 * The export endpoint is exposed via {@link buildAuditCsvUrl} rather than a
 * fetched function — the browser navigates to it so the Content-Disposition
 * attachment header drives the file dialog. The same axios instance can't
 * easily plumb a streaming download, and the route itself only needs the
 * query string.
 *
 * Diff PII columns (email / full_name / token_hash) are stored as sha256
 * fingerprints by the backend (chore PR #8 F4). The UI surfaces them as
 * `sha256:abcdef…` pills via {@link AuditDiffSha256Pill}.
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";

export const AUDIT_TARGET_TABLES = [
  "users",
  "teams",
  "memberships",
  "organizations",
  "projects",
  "scans",
  "scan_artifacts",
  "components",
  "component_versions",
  "scan_components",
  "vulnerabilities",
  "vulnerability_findings",
  "licenses",
  "license_findings",
  "obligations",
  "refresh_tokens",
  "password_reset_tokens",
  "license_fetch_cache",
] as const;

export type AuditTargetTable = (typeof AUDIT_TARGET_TABLES)[number];

export interface AuditLogItem {
  id: string;
  created_at: string;
  actor_user_id: string | null;
  actor_email: string | null;
  team_id: string | null;
  target_table: string;
  target_id: string | null;
  action: string;
  request_id: string | null;
  diff: Record<string, unknown> | null;
}

export interface AuditLogListPage {
  items: AuditLogItem[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface AuditSearchParams {
  actor_user_id?: string | null;
  target_table?: AuditTargetTable | null;
  action?: string | null;
  from?: string | null;
  to?: string | null;
  q?: string | null;
  page?: number;
  page_size?: number;
}

function toQueryParams(params: AuditSearchParams): Record<string, unknown> {
  return {
    actor_user_id: params.actor_user_id ?? undefined,
    target_table: params.target_table ?? undefined,
    action: params.action ?? undefined,
    from: params.from ?? undefined,
    to: params.to ?? undefined,
    q: params.q ?? undefined,
    page: params.page,
    page_size: params.page_size,
  };
}

export async function searchAdminAudit(
  params: AuditSearchParams = {},
  config?: AxiosRequestConfig,
): Promise<AuditLogListPage> {
  const { data } = await api.get<AuditLogListPage>("/v1/admin/audit", {
    ...config,
    params: toQueryParams(params),
  });
  return data;
}

export interface AuditCsvDownload {
  filename: string;
  blobUrl: string;
}

/**
 * Fetch the CSV export and turn it into a blob URL that the caller can
 * hand to an `<a download>` element. Doing this through axios keeps the
 * bearer token in the Authorization header (out of the URL / history /
 * server access logs).
 *
 * The streaming-vs-buffering trade-off: axios buffers the full response
 * into memory before resolving. The backend service caps the export at
 * 100k rows (`audit_export_too_large` Problem extension), so the worst
 * case is a few MB — well within browser tolerance.
 */
export async function downloadAdminAuditCsv(
  params: AuditSearchParams = {},
): Promise<AuditCsvDownload> {
  const response = await api.get<Blob>("/v1/admin/audit/export.csv", {
    params: toQueryParams(params),
    responseType: "blob",
  });
  // The backend sets `Content-Disposition: attachment; filename=...`; use
  // it when present, otherwise synthesize one from the window so a stuck
  // proxy can't strip the suggested name.
  const disposition =
    (response.headers as Record<string, string>)["content-disposition"] ?? "";
  const match = disposition.match(/filename="?([^";]+)"?/);
  const filename = match?.[1] ?? "audit_export.csv";
  const blobUrl = URL.createObjectURL(response.data as Blob);
  return { filename, blobUrl };
}
