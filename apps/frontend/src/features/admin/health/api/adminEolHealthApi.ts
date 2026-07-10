/**
 * Admin endoflife.date snapshot health REST surface — Phase M / PR M-3.
 *
 * Mirrors the backend contract:
 *   GET /v1/admin/eol/health → EolStatusOut
 *
 * Kept separate from the Trivy / KEV clients (same rationale: independent
 * evolution). Unlike the KEV panel there is no strict "never ran" empty
 * state — the vendored snapshot ships with every release, so the
 * config-derived fields (`snapshot_date`, `snapshot_origin`, `rule_count`,
 * `product_count`) are populated even before the first beat tick; only the
 * beat-derived fields are null then.
 */
import { api } from "@/lib/api";

export type EolSyncResult = "synced" | "skipped";
export type EolSnapshotOrigin = "vendored" | "feed";

export interface EolStatus {
  enabled: boolean;
  refresh_enabled: boolean;
  snapshot_date: string | null;
  snapshot_origin: EolSnapshotOrigin | null;
  rule_count: number;
  product_count: number;
  eol_flagged_total: number | null;
  last_synced_at: string | null;
  last_attempt_at: string | null;
  last_result: EolSyncResult | null;
  skipped_reason: string | null;
  stamped: number | null;
  cleared: number | null;
  duration_ms: number | null;
  next_refresh_at: string | null;
  feed_host: string | null;
}

export async function getAdminEolHealth(): Promise<EolStatus> {
  const { data } = await api.get<EolStatus>("/v1/admin/eol/health");
  return data;
}
