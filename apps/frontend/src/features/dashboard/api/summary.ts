// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

/**
 * `GET /v1/dashboard/summary`: portfolio totals computed on the server.
 *
 * Mirrors `schemas/dashboard.py`. The dashboard used to derive these numbers
 * in the browser from one page of `GET /v1/projects`, whose `size` caps at
 * 100. Above 100 projects that was not just incomplete but biased: the list is
 * ordered `updated_at DESC`, so the projects it dropped were the ones nobody
 * had touched lately, which is where risk collects. This endpoint has always
 * counted the whole portfolio.
 *
 * Two shapes of the same axis are returned on purpose. The `*_counts` pairs
 * ending in `severity_counts` / `category_counts` count COMPONENTS, which is
 * what a "N open vulnerabilities" KPI means. The `project_*` pairs count
 * PROJECTS, which is what the distribution charts mean, since each of their
 * segments deep-links to a filtered project list.
 */

export interface ScanStatusCounts {
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
}

export interface VulnerabilitySeverityCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
}

export interface LicenseCategoryCounts {
  prohibited: number;
  conditional: number;
  permissive: number;
  unknown: number;
}

/** Projects bucketed by their worst finding. `none` = scanned and clean. */
export interface ProjectSeverityCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
  none: number;
}

/** Projects bucketed by their worst licence verdict, in persisted names. */
export interface ProjectLicenseCounts {
  forbidden: number;
  conditional: number;
  allowed: number;
  unknown: number;
}

export interface DashboardRecentScan {
  scan_id: string;
  project_id: string;
  project_name: string;
  status: string;
  kind: string;
  finished_at: string | null;
  release: string | null;
}

export interface DashboardSummary {
  project_count: number;
  scan_status_counts: ScanStatusCounts;
  vulnerability_severity_counts: VulnerabilitySeverityCounts;
  license_category_counts: LicenseCategoryCounts;
  project_severity_counts: ProjectSeverityCounts;
  project_license_counts: ProjectLicenseCounts;
  pending_approvals_count: number;
  recent_scans: DashboardRecentScan[];
}

export async function getDashboardSummary(): Promise<DashboardSummary> {
  const { data } = await api.get<DashboardSummary>("/v1/dashboard/summary");
  return data;
}

export const DASHBOARD_SUMMARY_QUERY_KEY = ["dashboard", "summary"] as const;

/** Sum of the severities a person would call "open", i.e. excluding info. */
export function openVulnerabilityTotal(
  counts: VulnerabilitySeverityCounts,
): number {
  return counts.critical + counts.high + counts.medium + counts.low;
}

export function useDashboardSummary() {
  return useQuery({
    queryKey: DASHBOARD_SUMMARY_QUERY_KEY,
    queryFn: getDashboardSummary,
    // Same budget as the other three dashboard reads: the aggregate walks
    // every finding in scope and the route is rate limited per actor.
    staleTime: 60_000,
  });
}
