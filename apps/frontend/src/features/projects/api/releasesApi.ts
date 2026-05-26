/**
 * Releases wire surface — feature #28 Phase 1 (release snapshot viewing).
 *
 * A "release" is one *succeeded* scan of a project. The list is the project's
 * scan history, newest-first, each row carrying the headline numbers a viewer
 * needs to pick a snapshot to inspect (risk score, severity summary, gate
 * verdict, component count).
 *
 *   - GET /v1/projects/{id}/releases?page=&size= → ReleaseListResponse
 *
 * The wire types mirror the backend's `ReleaseSnapshot` 1:1 (snake_case).
 * `release` is frequently `null` (the scan was triggered without a version
 * label) and `gate_status` / `risk_score` may be `null` — callers render the
 * date / em-dash fallbacks rather than fabricating a value.
 *
 * Hard rules (CLAUDE.md):
 *   - All 4xx/5xx responses are `application/problem+json` and surface as
 *     {@link ProblemError} via the shared `api` interceptor.
 *   - No router import here. No state — pure REST.
 */
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Wire types — mirror the backend ReleaseSnapshot contract
// ---------------------------------------------------------------------------

/** Pass/fail verdict of the build gate evaluated against this snapshot's scan. */
export type ReleaseGateStatus = "pass" | "fail";

/**
 * Per-snapshot vulnerability-severity component counts. Every bucket is
 * present (a bucket may legitimately be 0); the table omits zero buckets when
 * rendering the compact C/H/M/L summary.
 */
export interface ReleaseSeveritySummary {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

export interface ReleaseSnapshot {
  /** The succeeded scan this snapshot points at — used as the `?scan=` anchor. */
  scan_id: string;
  /**
   * Optional release/version label this scan was triggered against (e.g.
   * `v1.2.3`). Frequently `null` — the UI falls back to the created date or an
   * em-dash so the row is never blank.
   */
  release: string | null;
  /** ISO-8601 instant the scan was created. */
  created_at: string;
  /** Computed 0..100 risk score for this snapshot, or `null` when unscored. */
  risk_score: number | null;
  severity_summary: ReleaseSeveritySummary;
  /** Build-gate verdict for this snapshot, or `null` when not evaluated. */
  gate_status: ReleaseGateStatus | null;
  /** Distinct component versions in this snapshot's scan. */
  component_count: number;
}

export interface ReleaseListResponse {
  items: ReleaseSnapshot[];
  total: number;
  page: number;
  size: number;
}

// ---------------------------------------------------------------------------
// List parameters + endpoint
// ---------------------------------------------------------------------------

export interface ListReleasesParams {
  /** 1-based page index (server default 1). */
  page?: number;
  /** Page size (server default applies when omitted). */
  size?: number;
}

export async function listProjectReleases(
  projectId: string,
  params: ListReleasesParams = {},
): Promise<ReleaseListResponse> {
  const query: Record<string, unknown> = {};
  if (params.page != null) query.page = params.page;
  if (params.size != null) query.size = params.size;
  const { data } = await api.get<ReleaseListResponse>(
    `/v1/projects/${projectId}/releases`,
    { params: query },
  );
  return data;
}
