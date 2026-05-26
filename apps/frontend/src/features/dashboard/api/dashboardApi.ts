/**
 * Dashboard summary REST surface — org/team risk portfolio.
 *
 * Mirrors the backend `DashboardSummary` schema served by
 *   GET /v1/dashboard/summary
 *
 * The backend always zero-fills every count bucket (no per-key null guarding
 * is needed on the client), so each bucket map below has a fixed key set.
 *
 * This module is intentionally free of TanStack Query so the same typed fetch
 * can be reused by mutations, imperative code paths, or unit tests — matching
 * the `projectsApi` / `adminDiskApi` convention.
 */
import { api } from "@/lib/api";
import type { ScanKind, ScanStatus } from "@/lib/projectsApi";

// ---------------------------------------------------------------------------
// Types — mirror the backend wire shapes (snake_case).
// ---------------------------------------------------------------------------

/** Vulnerability severity buckets, in display (most→least severe) order. */
export type DashboardSeverity =
  | "critical"
  | "high"
  | "medium"
  | "low"
  | "info";

/** License classification buckets returned by the dashboard endpoint. */
export type DashboardLicenseCategory =
  | "prohibited"
  | "conditional"
  | "permissive"
  | "unknown";

/** Scan lifecycle buckets surfaced as status cards. */
export type DashboardScanStatus = "queued" | "running" | "succeeded" | "failed";

export type ScanStatusCounts = Record<DashboardScanStatus, number>;
export type VulnerabilitySeverityCounts = Record<DashboardSeverity, number>;
export type LicenseCategoryCounts = Record<DashboardLicenseCategory, number>;

export interface RecentScan {
  scan_id: string;
  project_id: string;
  project_name: string;
  status: ScanStatus;
  kind: ScanKind;
  finished_at: string | null;
  /**
   * Optional release/version label the scan was triggered against (feature
   * #18), e.g. `v1.2.3`. `null` when no release was supplied.
   */
  release: string | null;
}

export interface DashboardSummary {
  project_count: number;
  scan_status_counts: ScanStatusCounts;
  vulnerability_severity_counts: VulnerabilitySeverityCounts;
  license_category_counts: LicenseCategoryCounts;
  pending_approvals_count: number;
  recent_scans: RecentScan[];
}

// ---------------------------------------------------------------------------
// Endpoint
// ---------------------------------------------------------------------------

export async function getDashboardSummary(): Promise<DashboardSummary> {
  const { data } = await api.get<DashboardSummary>("/v1/dashboard/summary");
  return data;
}
