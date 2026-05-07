/**
 * Admin DT-Connector REST surface — Phase 4 PR #14 §4.4.
 *
 * Mirrors `apps/backend/schemas/admin_ops.py`:
 *   - GET  /v1/admin/dt/status          → DTStatusOut
 *   - GET  /v1/admin/dt/orphans         → DTOrphanListPage
 *   - POST /v1/admin/dt/orphans/cleanup → OrphanCleanupEnqueued
 *   - POST /v1/admin/dt/health-check    → HealthProbeOut
 *
 * Errors propagate as ProblemError. Domain extensions surfaced by the
 * service layer (`dt_unreachable`, `dt_orphan_cleanup_in_progress`) are
 * whitelisted in `lib/problem.ts` so the toast key path stays graceful.
 */
import { api } from "@/lib/api";

export type BreakerState = "closed" | "open" | "half_open";

export interface DTStatus {
  state: BreakerState;
  fail_count: number;
  opened_at: string | null;
  last_check_at: string;
  version: string | null;
  last_error: string | null;
  /**
   * Optional — present when the auto-restart watchdog has tripped a
   * docker-compose restart in this monitor cycle.
   */
  auto_restart_attempted?: boolean;
}

export interface DTOrphanItem {
  dt_project_uuid: string;
  dt_project_name: string | null;
  dt_project_version: string | null;
}

export interface DTOrphanListPage {
  items: DTOrphanItem[];
  total: number;
  has_more: boolean;
}

export interface OrphanCleanupRequestPayload {
  /**
   * When omitted the Celery task scans the whole DT catalog and deletes
   * every orphan it finds. When populated, only the supplied UUIDs are
   * processed (matches the "select rows + cleanup" admin path).
   */
  dt_project_uuids?: string[];
}

export interface OrphanCleanupEnqueued {
  task_id: string;
  enqueued_at: string;
  count: number;
}

export interface HealthProbeOut {
  healthy: boolean;
  state_before: BreakerState;
  state_after: BreakerState;
  fail_count: number;
  auto_restart_attempted: boolean;
  error: string | null;
  checked_at: string;
}

export interface DTOrphanListParams {
  limit?: number;
  offset?: number;
}

export async function getDTStatus(): Promise<DTStatus> {
  const { data } = await api.get<DTStatus>("/v1/admin/dt/status");
  return data;
}

export async function listDTOrphans(
  params: DTOrphanListParams = {},
): Promise<DTOrphanListPage> {
  const { data } = await api.get<DTOrphanListPage>("/v1/admin/dt/orphans", {
    params: {
      limit: params.limit,
      offset: params.offset,
    },
  });
  return data;
}

export async function cleanupDTOrphans(
  payload: OrphanCleanupRequestPayload = {},
): Promise<OrphanCleanupEnqueued> {
  const { data } = await api.post<OrphanCleanupEnqueued>(
    "/v1/admin/dt/orphans/cleanup",
    {
      dt_project_uuids: payload.dt_project_uuids ?? [],
    },
  );
  return data;
}

export async function forceDTHealthCheck(): Promise<HealthProbeOut> {
  const { data } = await api.post<HealthProbeOut>("/v1/admin/dt/health-check");
  return data;
}
