/**
 * Admin Disk-Telemetry REST surface — Phase 4 PR #14 §4.6.
 *
 * Mirrors `apps/backend/schemas/admin_ops.py` `AdminDiskOut`.
 *   GET /v1/admin/disk → AdminDiskOut
 *
 * The backend computes used_pct + status (ok / degraded / down) against
 * configured thresholds (80% / 90%) so the UI never repeats the rule.
 * Per-item probe errors are surfaced via `AdminDiskItem.error` (non-null when
 * the mount is unreachable), not as Problem extension fields.
 */
import { api } from "@/lib/api";

export type DiskItemName = "workspace" | "trivy_db" | "postgres" | "redis";
export type DiskHealthStatus = "ok" | "degraded" | "down";

export interface AdminDiskItem {
  name: DiskItemName;
  path: string | null;
  total_bytes: number | null;
  used_bytes: number;
  free_bytes: number | null;
  used_pct: number | null;
  threshold_warning: number;
  threshold_critical: number;
  status: DiskHealthStatus;
  error: string | null;
}

export interface AdminDiskOut {
  items: AdminDiskItem[];
  collected_at: string;
}

export async function getAdminDisk(): Promise<AdminDiskOut> {
  const { data } = await api.get<AdminDiskOut>("/v1/admin/disk");
  return data;
}
