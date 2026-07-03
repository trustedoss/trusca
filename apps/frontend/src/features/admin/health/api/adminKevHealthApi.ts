/**
 * Admin CISA KEV feed health REST surface — Phase C / C2.
 *
 * Mirrors the backend contract:
 *   GET /v1/admin/kev/health → KevFeedStatusOut
 *
 * Kept separate from `adminHealthApi.ts` / `adminTrivyHealthApi.ts` (same
 * rationale as the Trivy client): each endpoint can evolve independently —
 * different polling cadence, different schema.
 *
 * Never-ran contract: when the sync beat has not executed yet (no state row),
 * the backend returns every nullable field as `null` — the panel renders the
 * EmptyState in that case. `enabled` and `feed_host` are always populated so
 * the operator still sees the effective configuration.
 */
import { api } from "@/lib/api";

export type KevSyncResult = "synced" | "skipped";

export interface KevFeedStatus {
  enabled: boolean;
  last_synced_at: string | null;
  last_attempt_at: string | null;
  last_result: KevSyncResult | null;
  skipped_reason: string | null;
  feed_count: number | null;
  listed: number | null;
  delisted: number | null;
  duration_ms: number | null;
  kev_flagged_total: number;
  next_refresh_at: string | null;
  feed_host: string;
}

export async function getAdminKevHealth(): Promise<KevFeedStatus> {
  const { data } = await api.get<KevFeedStatus>("/v1/admin/kev/health");
  return data;
}
