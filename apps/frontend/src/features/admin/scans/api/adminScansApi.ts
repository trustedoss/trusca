/**
 * Admin Scan-Queue REST surface — Phase 4 PR #14 §4.5.
 *
 * Mirrors `apps/backend/schemas/admin_ops.py`:
 *   - GET  /v1/admin/scans                  → AdminScanListPage
 *   - POST /v1/admin/scans/{scan_id}/cancel → AdminScanListItem
 *
 * The scan listing is keyed by status filter so cross-team operators can
 * narrow to running / queued / failed slices. The cancel endpoint surfaces
 * `scan_already_cancelled` and `scan_not_found` Problem extensions; both
 * are whitelisted in `lib/problem.ts` so the toast key path is graceful.
 */
import { api } from "@/lib/api";

export type AdminScanStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface AdminScanListItem {
  id: string;
  project_id: string;
  project_name: string;
  team_id: string;
  team_name: string;
  status: AdminScanStatus;
  kind: string;
  progress_percent: number;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  error_message: string | null;
  requested_by_user_id: string | null;
  created_at: string;
}

export interface AdminScanListPage {
  items: AdminScanListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminScanListParams {
  page?: number;
  page_size?: number;
  status?: AdminScanStatus | null;
}

export async function listAdminScans(
  params: AdminScanListParams = {},
): Promise<AdminScanListPage> {
  const { data } = await api.get<AdminScanListPage>("/v1/admin/scans", {
    params: {
      page: params.page,
      page_size: params.page_size,
      status: params.status ?? undefined,
    },
  });
  return data;
}

export async function cancelAdminScan(
  scanId: string,
): Promise<AdminScanListItem> {
  const { data } = await api.post<AdminScanListItem>(
    `/v1/admin/scans/${scanId}/cancel`,
  );
  return data;
}
