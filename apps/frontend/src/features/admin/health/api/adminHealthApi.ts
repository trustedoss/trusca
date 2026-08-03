// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Admin System-Health REST surface — Phase 4 PR #14 §4.8.
 *
 * Mirrors `apps/backend/schemas/admin_ops.py` `SystemHealthOut`.
 *   GET /v1/admin/health → SystemHealthOut
 *
 * Each component carries its own ok / degraded / down status plus a one-line
 * explanation. The explanation arrives twice: `detail_code` (+ `detail_params`)
 * for the states the backend describes itself, and `detail` as the English
 * prose. Render the code when there is one — see `healthDetailKey`.
 */
import { api } from "@/lib/api";

export type HealthStatus = "ok" | "degraded" | "down";

export type HealthComponentName =
  | "postgres"
  | "redis"
  | "celery"
  | "disk"
  | "active_scans"
  | "last_24h_errors";

export interface HealthComponent {
  name: HealthComponentName;
  status: HealthStatus;
  detail: string | null;
  /**
   * Stable id for a known state, e.g. `celery.no_workers`. Null when `detail`
   * holds a probe exception — those stay in the language the runtime produced
   * them in, because a translated stack trace is a worse diagnostic.
   */
  detail_code: string | null;
  detail_params: Record<string, string | number> | null;
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
