// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Admin malicious-snapshot health REST surface — #26 (MAL-2b).
 *
 * Mirrors the backend contract:
 *   GET /v1/admin/malicious/health → MaliciousStatusOut
 *
 * Like the EOL panel there is no "never ran" empty state: the snapshot ships
 * with every release, so the snapshot-derived fields are populated before the
 * first beat tick and only the beat-derived ones are null.
 */
import { api } from "@/lib/api";

export type MaliciousSyncResult = "synced" | "skipped";

export interface MaliciousStatus {
  enabled: boolean;
  refresh_enabled: boolean;
  snapshot_date: string | null;
  snapshot_stale: boolean;
  purl_count: number;
  ecosystems: string[];
  flagged_total: number | null;
  last_synced_at: string | null;
  last_attempt_at: string | null;
  last_result: MaliciousSyncResult | null;
  skipped_reason: string | null;
  stamped: number | null;
  /**
   * Rows that went clear → flagged on the last re-stamp: packages already in
   * stock that a new advisory caught up with. The number this beat exists to
   * produce — no scan would have surfaced them.
   */
  newly_flagged: number | null;
  next_refresh_at: string | null;
}

export async function getAdminMaliciousHealth(): Promise<MaliciousStatus> {
  const { data } = await api.get<MaliciousStatus>("/v1/admin/malicious/health");
  return data;
}
