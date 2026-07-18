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

import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";
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
  | "epss"
  | "reachable"
  | "component"
  /**
   * Composite triage ranking (KEV feature): KEV membership first, then
   * severity, then EPSS. Not a table column — it is offered by the toolbar's
   * sort select and is the tab's DEFAULT sort.
   */
  | "priority";
export type SortOrder = "asc" | "desc";

/**
 * Tri-state reachability filter token (v2.3 r2). Mirrors the backend's
 * `?reachable=` query parameter (`true` / `false` / `unknown`):
 *
 *   - `"true"`    → only findings whose vulnerable symbol is reachable on the
 *                   call graph (`reachable === true`).
 *   - `"false"`   → only findings an analyser proved NOT reachable
 *                   (`reachable === false`).
 *   - `"unknown"` → only not-analysed findings (`reachable IS NULL`).
 *
 * Omit the param to disable the filter. Any other value is a 422 on the wire,
 * so the UI only ever sends these three tokens.
 */
export type ReachabilityFilter = "true" | "false" | "unknown";

/**
 * Provenance of a finding's current status (v2.1 A2 — VEX import / consume).
 * `vex_import` marks a status that was auto-transitioned by an uploaded VEX
 * document; `manual` (or `null` on legacy rows) is the human PATCH workflow.
 * The column is free TEXT on the backend, so a future source could appear —
 * callers treat any unknown value the same as `manual` for display.
 */
export type AnalysisSource = "manual" | "vex_import";

/**
 * Provenance of the consuming VEX document, surfaced on a finding whose
 * `analysis_source === "vex_import"`. Every field is document-/analyst-supplied
 * and MUST be rendered through React's default text escaping (never
 * `dangerouslySetInnerHTML`). All fields are optional — the two VEX dialects
 * carry different provenance and producers omit fields.
 */
export interface VexOrigin {
  format?: "openvex" | "cyclonedx" | null;
  /** OpenVEX `@id` or CycloneDX `serialNumber`. */
  id?: string | null;
  author?: string | null;
  /** Document timestamp, verbatim from the source document. */
  timestamp?: string | null;
  /** The raw VEX status the matching statement carried. */
  vex_status?: string | null;
  /** ISO-8601 instant the import ran. */
  imported_at?: string | null;
  /** Forward-compat: keep any future provenance key without losing the typed ones. */
  [k: string]: unknown;
}

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
  /**
   * `true` when the CVE is listed in the CISA KEV (Known Exploited
   * Vulnerabilities) catalog — confirmed exploitation in the wild. Like EPSS,
   * this is a CVE-level attribute periodically refreshed from the catalog;
   * `false` simply means "not (yet) listed".
   */
  kev: boolean;
  /**
   * CISA's remediation due date for a KEV-listed CVE (ISO date, e.g.
   * "2026-07-15"). `null` when the CVE is not in the catalog or the catalog
   * entry carries no due date.
   */
  kev_due_date: string | null;
  summary: string | null;
  status: VulnFindingStatus;
  /**
   * Provenance of the row's current status (v2.1 A2). `vex_import` when an
   * uploaded VEX document drove the transition; `manual` / `null` otherwise.
   * Backs the "suppressed via VEX" inline filter and the row's VEX marker.
   */
  analysis_source: AnalysisSource | null;
  /**
   * Tri-state reachability signal (v2.3 r2). `true` = the vulnerable symbol is
   * reachable on the project's call graph (a priority signal); `false` = an
   * analyser ran and concluded it is NOT reachable; `null` = not analysed (no
   * reachability run, or the package was out of the analyser's language scope).
   * Drives the row's {@link ReachabilityBadge}, the `?reachable=` filter, and
   * the `sort=reachable` ranking.
   */
  reachable: boolean | null;
  /**
   * Identifier of the analyser that produced `reachable` ("govulncheck" today).
   * `null` when `reachable` is `null`.
   */
  reachability_source: string | null;
  /**
   * ISO-8601 instant the reachability signal was last written, or `null` until
   * a reachability run touches this finding.
   */
  reachability_analyzed_at: string | null;
  affected_component_count: number;
  /**
   * Name of the component_version THIS finding row is FK-pinned to (one row =
   * one (cv × CVE) pairing). NOT an aggregate across the CVE's OTHER affected
   * cvs — when `affected_component_count > 1` the additional cvs surface via
   * the drawer's `affected_components` list, and the UI hints at them on the
   * row with a `+N-1` suffix. `null` only on legacy rows whose target was
   * since CASCADE-deleted (effectively unreachable in practice).
   * Backend follow-up to W4-B.
   */
  affected_component_name: string | null;
  /** Version string of the row's pinned cv (same single-cv rationale). */
  affected_component_version: string | null;
  /**
   * SPDX id of the worst-rank license attached to the row's pinned cv in this
   * scan. Same aggregation rule as `affected_component_license_category` so
   * the SPDX label and the policy badge always agree. `null` when the cv has
   * no license finding OR when the worst-rank license is a `LicenseRef-*`
   * custom license (we never invent an SPDX string).
   */
  affected_component_license: string | null;
  /**
   * Policy category of the SPDX id reported in `affected_component_license`
   * (back-compat duplicate of the longstanding `component_license_category`).
   * `null` only when `affected_component_license` is also `null`; otherwise
   * always matches the badge rendered by the components tab for the same cv.
   */
  affected_component_license_category: LicenseCategoryName | null;
  /**
   * Worst-category license classification of the finding's component_version
   * (W2 #33). When the component_version carries multiple licenses, the
   * backend collapses them with the same `_license_rank_case` used by the
   * Components tab — so a row's License axis is identical across the two
   * tabs. `"unknown"` covers both "no license finding" and a LEFT-JOIN miss;
   * `null` is never on the wire (defended in the schema), so the UI treats
   * the field as always-present. Kept alongside the new null-bearing
   * `affected_component_license_category` for back-compat.
   */
  component_license_category: LicenseCategoryName;
  discovered_at: string;
  updated_at: string;
}

export interface VulnerabilityListResponse {
  items: VulnerabilityListItem[];
  total: number;
  limit: number;
  offset: number;
  /**
   * Count of findings per severity bucket for the resolved snapshot,
   * ignoring list filters. Backed by the backend's grouped query over
   * `vulnerability_findings × vulnerabilities.severity`, so a `null`/empty
   * map means the scan has zero findings (not a transport error). The
   * Vulnerabilities tab uses this to drive its summary card — the card
   * stays stable while the paginated rows below reflect the active filter.
   */
  severity_distribution?: Partial<Record<VulnSeverity, number>>;
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

/**
 * Minimum-safe-upgrade recommendation for a finding's component (v2.2 2.2-a3).
 * Mirrors `schemas.vulnerability_detail.UpgradeRecommendation`.
 *
 * `recommended_version` is the semver maximum of the component's open findings'
 * fix versions — the lowest version that resolves all of them. `null` (with a
 * `reason`) when no concrete version could be recommended; the drawer then shows
 * a "no recommendation" hint instead of a misleading partial upgrade.
 *
 * `direct` / `max_severity` / `max_epss` are priority signals (not used to
 * compute the version) so the UI can flag the highest-leverage upgrade.
 */
export type UpgradeRecommendationReason =
  | "ok"
  | "no_fix_version"
  | "unparseable_version"
  | "no_open_findings";

export interface UpgradeRecommendation {
  recommended_version: string | null;
  reason: UpgradeRecommendationReason;
  direct: boolean;
  max_severity: VulnSeverity | null;
  max_epss: number | null;
  finding_count: number;
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
  /**
   * CISA KEV catalog membership (same semantics as the list item's `kev`).
   * Optional (not `boolean`) because the detail schema may lag the list
   * contract by a deploy — callers treat a missing field as `false`.
   */
  kev?: boolean;
  /** CISA remediation due date (ISO date) — see the list item's field. */
  kev_due_date?: string | null;
  summary: string | null;
  details: string | null;
  references: VulnerabilityReference[];
  published_at: string | null;
  status: VulnFindingStatus;
  analysis_state: string | null;
  analysis_justification: string | null;
  /** Provenance of the current status (v2.1 A2): `vex_import` | `manual` | null. */
  analysis_source: AnalysisSource | null;
  /** Consuming VEX document provenance when `analysis_source === "vex_import"`. */
  vex_origin: VexOrigin | null;
  analyst_user_id: string | null;
  analyzed_at: string | null;
  /**
   * Tri-state reachability signal (v2.3 r2): `true` = reachable; `false` =
   * analysed and NOT reachable; `null` = not analysed. Drives the drawer's
   * {@link ReachabilityBadge}.
   */
  reachable: boolean | null;
  /** Analyser that produced `reachable` ("govulncheck"); `null` when unanalysed. */
  reachability_source: string | null;
  /** ISO-8601 instant the reachability signal was last written; `null` if unanalysed. */
  reachability_analyzed_at: string | null;
  affected_components: AffectedComponent[];
  status_history: VulnerabilityStatusHistoryEntry[];
  /** Minimum-safe-upgrade recommendation for this finding's component (v2.2 2.2-a3). */
  upgrade_recommendation: UpgradeRecommendation | null;
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
  /**
   * Tri-state reachability filter (v2.3 r2). `"true"` / `"false"` / `"unknown"`
   * map to `reachable === true` / `=== false` / `IS NULL`. Omit to disable.
   */
  reachable?: ReachabilityFilter;
  /**
   * License-category buckets (W2 #33). Multi-value; the backend keeps any
   * finding whose component's worst-category license is in the set. The
   * "unknown" bucket also includes findings whose component_version has no
   * license finding. Unknown values are dropped server-side (no 422); a
   * filter with only unknown values yields an empty page.
   */
  license_category?: LicenseCategoryName[];
  /**
   * Pin the read to a specific succeeded scan (feature #28 snapshot anchoring).
   * Omit → the project's latest succeeded scan (unchanged default). An invalid /
   * cross-project / non-succeeded id is a 404 problem+json on the wire.
   */
  scanId?: string;
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
  // Only forward one of the three legal reachability tokens. A hand-edited URL
  // with anything else is dropped here so we never trip the backend's 422.
  if (
    params.reachable === "true" ||
    params.reachable === "false" ||
    params.reachable === "unknown"
  ) {
    out.reachable = params.reachable;
  }
  // W2 #33 — multi-value license_category. The repeat-key axios serializer
  // (`paramsSerializer: { indexes: null }` below) handles array → repeated key.
  if (params.license_category && params.license_category.length > 0) {
    out.license_category = params.license_category;
  }
  if (params.scanId != null && params.scanId.length > 0) {
    out.scan_id = params.scanId;
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
// Upgrade clusters (W9-#53 — "Group by upgrade")
// ---------------------------------------------------------------------------

/**
 * One open finding inside an {@link UpgradeCluster}. Mirrors
 * `schemas.vulnerability_detail.UpgradeClusterFinding` (snake_case). The
 * `finding_id` keys the SAME per-finding detail drawer the flat list opens
 * (`GET /v1/vulnerability_findings/{id}`), so a cluster row and a flat row
 * that reference the same finding land on identical drawer state.
 *
 * `findings` arrive pre-sorted (severity desc, then `cve_id` asc); the UI
 * renders them verbatim.
 */
export interface UpgradeClusterFinding {
  finding_id: string;
  cve_id: string;
  severity: VulnSeverity;
  status: VulnFindingStatus;
  epss_score: number | null;
  kev: boolean;
  fixed_version: string | null;
}

/**
 * A minimum-safe-upgrade cluster: every open finding on ONE component_version
 * that a single version bump would resolve. Mirrors
 * `schemas.vulnerability_detail.UpgradeCluster`.
 *
 * `recommended_version` is the semver maximum of the cluster findings' fix
 * versions — the lowest bump that clears them all. It is `null` (with a
 * `reason` other than `"ok"`) when we decline to recommend a partial or
 * unparseable upgrade. `direct` / `max_severity` / `max_epss` are priority
 * signals so the UI can flag the highest-leverage bump.
 */
export interface UpgradeCluster {
  component_version_id: string;
  component_name: string;
  component_purl: string | null;
  current_version: string;
  recommended_version: string | null;
  reason: UpgradeRecommendationReason;
  direct: boolean;
  max_severity: VulnSeverity | null;
  max_epss: number | null;
  finding_count: number;
  findings: UpgradeClusterFinding[];
}

/**
 * Response of `GET /v1/projects/{id}/vulnerabilities/upgrade-clusters`. The
 * clusters arrive most-actionable first (a direct dependency with a computed
 * upgrade, high severity + EPSS, ranks above an indirect one with no fix). By
 * contract `total_findings` equals the sum of every cluster's `finding_count`
 * AND the scan's open-finding total — the header uses it for a single count.
 * `scan_id` echoes the resolved snapshot (the pinned `?scan_id=` or the latest
 * succeeded scan); `null` with empty `clusters` when the project has no
 * succeeded scan.
 */
export interface UpgradeClusterListResponse {
  scan_id: string | null;
  total_findings: number;
  clusters: UpgradeCluster[];
}

export interface ListUpgradeClustersParams {
  /**
   * Pin the read to a specific succeeded scan (feature #28 snapshot anchoring),
   * threading the same anchor the flat list uses. Omit → latest succeeded scan.
   * An invalid / cross-project / non-succeeded id is a 404 problem+json.
   */
  scanId?: string;
}

export async function listUpgradeClusters(
  projectId: string,
  params: ListUpgradeClustersParams = {},
): Promise<UpgradeClusterListResponse> {
  const query: Record<string, unknown> = {};
  if (params.scanId != null && params.scanId.length > 0) {
    query.scan_id = params.scanId;
  }
  const { data } = await api.get<UpgradeClusterListResponse>(
    `/v1/projects/${projectId}/vulnerabilities/upgrade-clusters`,
    { params: query },
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

// ---------------------------------------------------------------------------
// Bulk status transition (W2 #33b)
// ---------------------------------------------------------------------------

/**
 * Maximum finding ids per single bulk call. Mirrors the backend's
 * `BULK_TRANSITION_MAX` so the UI rejects oversize selections client-side
 * (instead of round-tripping a 422). The cap absorbs ~90% of practical
 * triage gestures; larger sweeps split into chunks of this size.
 */
export const BULK_TRANSITION_MAX = 200;

export interface BulkStatusUpdateBody {
  finding_ids: string[];
  target_status: VulnFindingStatus;
  justification?: string;
}

/**
 * Per-row outcome of one entry in a bulk transition. `success` is the most
 * concise signal; `status_code` carries the HTTP-style result code so the UI
 * can disambiguate 404 (id missing in this project) / 403 (role insufficient
 * for `→ suppressed`) / 422 (transition matrix forbids it). `allowed_to` is
 * populated on 422 so a future per-row hint can offer a legal alternative.
 */
export interface BulkStatusResult {
  finding_id: string;
  success: boolean;
  status_code: number;
  error: string | null;
  detail: string | null;
  allowed_to: VulnFindingStatus[] | null;
}

export interface BulkStatusResponse {
  target_status: VulnFindingStatus;
  total: number;
  succeeded: number;
  failed: number;
  results: BulkStatusResult[];
}

/**
 * POST `/v1/projects/{id}/vulnerabilities:bulk-transition`. Returns 200 with
 * a per-row outcome envelope; envelope-level 4xx (empty list, > cap, unknown
 * enum, cross-team caller) arrives via the shared `api` interceptor as a
 * `ProblemError`.
 */
export async function bulkTransitionVulnerabilities(
  projectId: string,
  body: BulkStatusUpdateBody,
): Promise<BulkStatusResponse> {
  const { data } = await api.post<BulkStatusResponse>(
    `/v1/projects/${projectId}/vulnerabilities:bulk-transition`,
    body,
  );
  return data;
}
