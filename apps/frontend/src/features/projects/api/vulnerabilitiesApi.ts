/**
 * Vulnerability triage wire surface — Phase 3 PR #11.
 *
 * Three endpoints land on the project Vulnerabilities tab:
 *
 *   - GET   /v1/projects/{id}/vulnerabilities          → VulnerabilityListResponse
 *   - GET   /v1/vulnerability_findings/{id}            → VulnerabilityDetail
 *   - PATCH /v1/vulnerability_findings/{id}/status     → VulnerabilityDetail
 *
 * The wire types mirror `apps/backend/schemas/vulnerability_detail.py` 1:1
 * (snake_case). All four/5xx responses arrive as
 * `application/problem+json` and surface as {@link ProblemError} via the
 * shared `api` interceptor (PR #6).
 *
 * 422 (invalid transition) carries an `allowed_to` extension — we narrow the
 * generic `ProblemError` to {@link InvalidTransitionError} so the drawer can
 * disable buttons proactively without a second round trip.
 */
import { api } from "@/lib/api";
import { ProblemError } from "@/lib/problem";

import type { VulnerabilityStatus } from "@/features/projects/lib/vulnerabilityTransitions";

// ---------------------------------------------------------------------------
// Wire types — mirror apps/backend/schemas/vulnerability_detail.py
// ---------------------------------------------------------------------------

export type VulnSeverity =
  | "critical"
  | "high"
  | "medium"
  | "low"
  | "info"
  | "unknown";

/** Mirror of `STATUS_TRANSITIONS` keys — re-export for convenience. */
export type VulnFindingStatus = VulnerabilityStatus;

export type VulnerabilitySortKey =
  | "severity"
  | "cvss"
  | "status"
  | "discovered_at"
  | "epss";
export type SortOrder = "asc" | "desc";

export interface VulnerabilityListItem {
  id: string;
  cve_id: string;
  severity: VulnSeverity;
  cvss_score: number | null;
  /**
   * EPSS probability (0–1) that this CVE is exploited in the wild over the
   * next 30 days. `null` when EPSS has no entry for the CVE. Surfaced as a
   * first-class column / sort key / filter alongside CVSS (v2.1).
   */
  epss_score: number | null;
  /** EPSS percentile (0–1) — rank of this score among all scored CVEs. */
  epss_percentile: number | null;
  summary: string | null;
  status: VulnFindingStatus;
  affected_component_count: number;
  discovered_at: string;
  updated_at: string;
}

export interface VulnerabilityListResponse {
  items: VulnerabilityListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface AffectedComponent {
  component_version_id: string;
  name: string;
  version: string;
  purl: string | null;
  fixed_version: string | null;
}

/**
 * Reference type. The backend schema declares `references: list[Any]` because
 * upstream sources (NVD, GHSA) return varied shapes. We type the common
 * `{type, url}` form and accept unknown so callers can defensively narrow.
 */
export interface VulnerabilityReference {
  type?: string;
  url?: string;
  /** Allow extra fields without losing the typed common case. */
  [k: string]: unknown;
}

export interface VulnerabilityStatusHistoryEntry {
  actor_user_id: string | null;
  /** Always 'create' or 'update'. */
  action: string;
  /** null on the synthesized CREATE entry. */
  previous_status: VulnFindingStatus | null;
  new_status: VulnFindingStatus;
  created_at: string;
  request_id: string | null;
}

export interface VulnerabilityDetail {
  id: string;
  project_id: string;
  scan_id: string;
  cve_id: string;
  severity: VulnSeverity;
  cvss_score: number | null;
  cvss_vector: string | null;
  /** EPSS probability (0–1) of exploitation in the next 30 days. */
  epss_score: number | null;
  /** EPSS percentile (0–1) — rank among all scored CVEs. */
  epss_percentile: number | null;
  summary: string | null;
  details: string | null;
  references: VulnerabilityReference[];
  published_at: string | null;
  status: VulnFindingStatus;
  analysis_state: string | null;
  analysis_justification: string | null;
  analyst_user_id: string | null;
  analyzed_at: string | null;
  affected_components: AffectedComponent[];
  status_history: VulnerabilityStatusHistoryEntry[];
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// List parameters
// ---------------------------------------------------------------------------

export interface ListVulnerabilitiesParams {
  limit?: number;
  offset?: number;
  search?: string;
  severity?: VulnSeverity[];
  status?: VulnFindingStatus[];
  sort?: VulnerabilitySortKey;
  order?: SortOrder;
  /**
   * EPSS threshold (0–1). When set, the backend keeps findings whose
   * `epss_score >= min_epss` and drops NULL-EPSS rows entirely.
   */
  min_epss?: number;
}

function listVulnerabilitiesQuery(
  params: ListVulnerabilitiesParams,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (params.limit != null) out.limit = params.limit;
  if (params.offset != null) out.offset = params.offset;
  if (params.search != null && params.search.length > 0) {
    out.search = params.search;
  }
  if (params.severity && params.severity.length > 0) {
    out.severity = params.severity;
  }
  if (params.status && params.status.length > 0) {
    out.status = params.status;
  }
  if (params.sort != null) out.sort = params.sort;
  if (params.order != null) out.order = params.order;
  // Only send a finite, in-range threshold. 0 is a meaningful lower bound
  // (keep every scored CVE, drop NULL), so we explicitly allow it.
  if (
    params.min_epss != null &&
    Number.isFinite(params.min_epss) &&
    params.min_epss >= 0 &&
    params.min_epss <= 1
  ) {
    out.min_epss = params.min_epss;
  }
  return out;
}

export async function listProjectVulnerabilities(
  projectId: string,
  params: ListVulnerabilitiesParams = {},
): Promise<VulnerabilityListResponse> {
  const { data } = await api.get<VulnerabilityListResponse>(
    `/v1/projects/${projectId}/vulnerabilities`,
    {
      params: listVulnerabilitiesQuery(params),
      // Repeat-key style for list params so FastAPI parses them as list[str].
      paramsSerializer: { indexes: null },
    },
  );
  return data;
}

export async function getVulnerabilityFinding(
  findingId: string,
): Promise<VulnerabilityDetail> {
  const { data } = await api.get<VulnerabilityDetail>(
    `/v1/vulnerability_findings/${findingId}`,
  );
  return data;
}

export interface UpdateVulnerabilityStatusBody {
  status: VulnFindingStatus;
  justification?: string;
  /** ISO8601 echo of the prior `updated_at` for optimistic concurrency. */
  if_match?: string;
}

export async function updateVulnerabilityStatus(
  findingId: string,
  body: UpdateVulnerabilityStatusBody,
): Promise<VulnerabilityDetail> {
  const { data } = await api.patch<VulnerabilityDetail>(
    `/v1/vulnerability_findings/${findingId}/status`,
    body,
  );
  return data;
}

// ---------------------------------------------------------------------------
// 422 narrow: extract `allowed_to` extension from the RFC 7807 problem body.
// ---------------------------------------------------------------------------

/**
 * Pull the `allowed_to` extension from a 422 ProblemError. Returns null if
 * the error is not 422 or doesn't carry the extension.
 *
 * Notes:
 *   - `ProblemDetails` (lib/problem.ts) only types the four standard fields.
 *     The `allowed_to` extension lives on the raw axios response body, which
 *     we re-inspect here. Adding it to `ProblemDetails` would either
 *     pollute every caller's type or require a generic — neither is worth
 *     it for a single endpoint's extension.
 *   - We accept anything that exposes a `.problem` shape so the helper works
 *     regardless of whether the caller saw the error before us.
 */
export function extractAllowedTo(error: unknown): VulnFindingStatus[] | null {
  if (!(error instanceof ProblemError)) return null;
  if (error.status !== 422) return null;
  const problem = error.problem as Record<string, unknown> | null;
  if (!problem || typeof problem !== "object") return null;
  const raw = (problem as { allowed_to?: unknown }).allowed_to;
  if (!Array.isArray(raw)) return null;
  return raw.filter(
    (value): value is VulnFindingStatus =>
      typeof value === "string" &&
      [
        "new",
        "analyzing",
        "exploitable",
        "not_affected",
        "false_positive",
        "suppressed",
        "fixed",
      ].includes(value),
  );
}

/**
 * `true` when the error is a 409 conflict (caller passed an `if_match` value
 * that no longer matches the current `updated_at`). The drawer surfaces a
 * "Reload" action when this happens.
 */
export function isConflictError(error: unknown): boolean {
  return error instanceof ProblemError && error.status === 409;
}
