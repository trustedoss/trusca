/**
 * Admin Trivy DB Health REST surface — W6-#43e.
 *
 * Mirrors `apps/backend/schemas/admin_ops.py` `TrivyDbStatusOut`.
 *   GET /v1/admin/trivy/health → TrivyDbStatusOut
 *
 * The panel sits beside the existing admin/health probe grid; we keep the
 * client file separate from `adminHealthApi.ts` so the two endpoints can
 * evolve independently (different polling cadence, different schemas).
 *
 * Empty-state contract: when the Trivy DB has not been downloaded yet on
 * the worker, the backend returns `last_update: null` + `freshness: "unknown"`
 * with the config fields (cache_dir / repository / refresh_interval_hours)
 * still populated, so the FE can render the EmptyState with operator
 * context.
 */
import { api } from "@/lib/api";

export type TrivyDbFreshness = "fresh" | "stale" | "very_stale" | "unknown";

export interface TrivyDbStatus {
  last_update: string | null;
  next_refresh_at: string | null;
  vuln_count: number | null;
  db_version: string | null;
  db_size_bytes: number | null;
  refresh_interval_hours: number;
  freshness: TrivyDbFreshness;
  cache_dir: string;
  repository: string;
}

export async function getAdminTrivyHealth(): Promise<TrivyDbStatus> {
  const { data } = await api.get<TrivyDbStatus>("/v1/admin/trivy/health");
  return data;
}
