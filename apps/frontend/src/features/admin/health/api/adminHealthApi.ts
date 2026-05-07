/**
 * Admin System-Health REST surface — Phase 4 PR #14 §4.8.
 *
 * Mirrors `apps/backend/schemas/admin_ops.py` `SystemHealthOut`.
 *   GET /v1/admin/health → SystemHealthOut
 *
 * Each component carries its own ok / degraded / down status plus a
 * one-line `detail` string the UI renders verbatim.
 */
import { api } from "@/lib/api";

export type HealthStatus = "ok" | "degraded" | "down";

export type HealthComponentName =
  | "postgres"
  | "redis"
  | "celery"
  | "dt"
  | "disk"
  | "active_scans"
  | "last_24h_errors";

export interface HealthComponent {
  name: HealthComponentName;
  status: HealthStatus;
  detail: string | null;
  value: number | null;
}

export interface SystemHealthOut {
  components: HealthComponent[];
  updated_at: string;
}

export async function getAdminHealth(): Promise<SystemHealthOut> {
  const { data } = await api.get<SystemHealthOut>("/v1/admin/health");
  return data;
}
