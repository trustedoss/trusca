/**
 * Project detail wire surface — Phase 3 PR #10.
 *
 * Three endpoints land on the project detail page (Overview / Components):
 *
 *   - GET /v1/projects/{id}/overview      → ProjectOverviewResponse
 *   - GET /v1/projects/{id}/components    → ComponentListResponse
 *   - GET /v1/components/{id}             → ComponentDetailResponse
 *
 * The wire types mirror `apps/backend/schemas/project_detail.py` 1:1
 * (snake_case). Hooks in `./useProjectOverview.ts`, `./useComponents.ts`, and
 * `./useComponent.ts` wrap these in TanStack Query.
 *
 * Hard rules (CLAUDE.md):
 *   - All four/5xx responses are `application/problem+json` and surface as
 *     {@link ProblemError} via the shared `api` interceptor.
 *   - No router import here. No state — pure REST.
 */
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Wire types — mirror apps/backend/schemas/project_detail.py
// ---------------------------------------------------------------------------

export type ComponentSeverity =
  | "critical"
  | "high"
  | "medium"
  | "low"
  | "info"
  | "none";

export type LicenseCategoryName =
  | "forbidden"
  | "conditional"
  | "allowed"
  | "unknown";

/**
 * The requesting user's effective role *within this project's owning team*.
 * NOT the global JWT role (which only yields `super_admin`/`developer`): a
 * membership-based `team_admin` of this project's team must see `team_admin`
 * here so the frontend can gate team-scoped actions like vulnerability
 * suppression (BUG-005). Mirrors `schemas/project_detail.py::TeamScopedRole`.
 */
export type TeamScopedRole = "super_admin" | "team_admin" | "developer";

export interface ScanSummary {
  id: string;
  kind: string;
  status: string;
  progress_percent: number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  /**
   * Release / version label sourced from the scan's `scan_metadata.release`
   * (the same field the Versions tab renders). `null` when the scan was run
   * without a release label.
   */
  release: string | null;
}

export interface ProjectOverviewResponse {
  project_id: string;
  project_name: string;
  total_components: number;
  /** Counts per severity bucket. May omit zero buckets — render with fallback. */
  severity_distribution: Partial<Record<ComponentSeverity, number>>;
  /** Counts per license category. */
  license_distribution: Partial<Record<LicenseCategoryName, number>>;
  /** Overall risk 0–100 = max(security_score, license_score). Non-saturating. */
  risk_score: number;
  /** Security axis 0–100, driven by the worst CVE severity present (band-by-severity). */
  security_score: number;
  /** License axis 0–100; `conditional` alone caps at Medium (≤49), never Critical. */
  license_score: number;
  recent_scans: ScanSummary[];
  /** Timestamp of the latest scan *attempt* (may be a failed scan). */
  last_scan_at: string | null;
  /**
   * Timestamp of the latest *succeeded* scan — the scan the SBOM export is
   * actually generated from. `null` when there is no succeeded scan yet. The
   * SBOM tab labels its "Latest scan" with this (not `last_scan_at`) so the
   * label matches the artifact a download would produce.
   */
  last_succeeded_scan_at: string | null;
  /**
   * #35 Surface B — whether DT's vulnerability DB held data WHEN the anchored
   * scan ran. `false` → 0 CVEs means "no data", not "safe" (show a caveat);
   * `true` → an empty Security axis is a real clean result; `null` → unknown
   * (no succeeded scan or a scan predating the capture) → show no caveat.
   */
  vuln_data_available: boolean | null;
  /**
   * The requesting user's effective role within this project's owning team.
   * Used (not the global JWT role) to gate team-scoped actions such as
   * vulnerability suppression (BUG-005).
   */
  current_user_role: TeamScopedRole;
  /**
   * Whether a git credential is stored for this project (feature #18). Presence
   * only — the value is never returned. Mirrors
   * `ProjectPublic.has_git_credential`.
   */
  has_git_credential: boolean;
}

export interface ComponentSummary {
  /** component_version id (the scan-bound row); used everywhere as the row key. */
  id: string;
  component_id: string;
  name: string;
  version: string;
  purl: string | null;
  license: string | null;
  license_category: LicenseCategoryName;
  severity_max: ComponentSeverity;
  vulnerability_count: number;
  /**
   * Dependency graph depth from the scanned root (W2 #31). ``1`` = direct,
   * ``2+`` = transitive. ``null`` when the scan recorded no dependency graph
   * (e.g. an SBOM without dependency edges) — render as "—" with muted styling.
   * When a component version is reachable by multiple paths the *shallowest*
   * wins.
   */
  depth: number | null;
  /**
   * Convenience flag mirroring ``depth === 1``. Server-side OR across paths
   * when several scan paths reach the same version. ``false`` for transitive
   * deps and for the depth-null bucket.
   */
  direct: boolean;
  /**
   * BD-style "Usage" facet (W2 #31). ``required`` / ``optional`` is the raw
   * scope of the chosen (shallowest) path. ``null`` when cdxgen produced no
   * scope on any edge — common for SBOMs that don't encode it; render as "—".
   * Aggregation across multiple reaching paths prefers ``required`` over
   * ``optional`` so the strictest wins.
   */
  dependency_scope: "required" | "optional" | null;
}

export interface ComponentListResponse {
  items: ComponentSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface VulnerabilityRef {
  cve_id: string;
  severity: string;
  cvss: number | null;
  /** EPSS probability (0–1) of exploitation in the next 30 days, or null. */
  epss_score: number | null;
  /** EPSS percentile (0–1) — rank among all scored CVEs, or null. */
  epss_percentile: number | null;
  title: string;
  description: string | null;
  fixed_version: string | null;
}

/**
 * M-20 — compact license-obligation reference attached to a component detail.
 * Lean projection of the obligations catalog; the full drawer shape stays on
 * the Obligations tab endpoints. Ordered by (kind, license, id) server-side.
 */
export interface ObligationRef {
  id: string;
  /** Free-form catalog kind (e.g. `attribution`, `source-disclosure`). */
  kind: string;
  /** Human-readable obligation text. */
  text: string;
  /**
   * Optional URL with further explanation. The backend does NOT scheme-filter
   * — render as a clickable link only for http/https.
   */
  link: string | null;
  /** Display id of the parent license (SPDX short id or license name). */
  license: string;
}

export interface ComponentDetailResponse {
  id: string;
  project_id: string;
  name: string;
  version: string;
  purl: string | null;
  license: string | null;
  license_category: LicenseCategoryName;
  severity_max: ComponentSeverity;
  vulnerabilities: VulnerabilityRef[];
  /**
   * M-20 — duties carried by every license observed for this component in
   * the anchoring scan. Empty when the component has no license, the license
   * is not in the catalog, or the catalog defines no obligations for it.
   */
  obligations: ObligationRef[];
  raw_data: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  /**
   * Dependency depth from the scanned root (W2 #31). 1 = direct, 2+ =
   * transitive, ``null`` when the scan carried no dependency graph.
   */
  depth: number | null;
  /** ``true`` when this component is a direct dependency (depth 1). */
  direct: boolean;
  /**
   * BD-style "Usage" of the chosen (shallowest) path. ``null`` when the
   * scan didn't encode a scope on the edge.
   */
  dependency_scope: "required" | "optional" | null;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export type ComponentSortKey = "name" | "severity" | "license";
export type SortOrder = "asc" | "desc";

/**
 * BD-style "Usage" filter bucket (W2 #31). ``unspecified`` maps to the NULL
 * scope bucket on the backend (cdxgen often produces no scope on edges). The
 * server drops unknown values silently — a query that filters only by unknown
 * values returns an empty page (not a 422).
 */
export type DependencyScopeFilter = "required" | "optional" | "unspecified";

export interface ListComponentsParams {
  limit?: number;
  offset?: number;
  search?: string;
  severity?: ComponentSeverity[];
  license_category?: LicenseCategoryName[];
  sort?: ComponentSortKey;
  order?: SortOrder;
  /**
   * Dependency-type 3-state (W2 #31). ``true`` keeps only direct dependencies
   * (depth 1), ``false`` only transitive (or graph-less) ones. Omit / ``null``
   * to include both.
   */
  direct?: boolean | null;
  /**
   * "Usage" facet (W2 #31). Multiple values OR together. Omit / empty array
   * to include all buckets.
   */
  dependency_scope?: DependencyScopeFilter[];
  /**
   * Pin the read to a specific succeeded scan (feature #28 snapshot anchoring).
   * Omit → the project's latest succeeded scan (unchanged default). An invalid /
   * cross-project / non-succeeded id is a 404 problem+json on the wire.
   */
  scanId?: string;
}

/**
 * Build the query-string params object axios accepts. We let axios serialize
 * arrays into repeated `?severity=critical&severity=high` keys (FastAPI
 * `list[str]` query parameter convention).
 */
function listComponentsQuery(
  params: ListComponentsParams,
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
  if (params.license_category && params.license_category.length > 0) {
    out.license_category = params.license_category;
  }
  if (params.sort != null) out.sort = params.sort;
  if (params.order != null) out.order = params.order;
  // W2 #31 — only emit `direct` when the caller has an opinion (null/undefined
  // means "include both"). axios drops `undefined` from the query string for
  // us, but we omit it explicitly so the wire shape is obvious.
  if (params.direct === true || params.direct === false) {
    out.direct = params.direct;
  }
  if (params.dependency_scope && params.dependency_scope.length > 0) {
    out.dependency_scope = params.dependency_scope;
  }
  if (params.scanId != null && params.scanId.length > 0) {
    out.scan_id = params.scanId;
  }
  return out;
}

export async function getProjectOverview(
  projectId: string,
  options: { scanId?: string } = {},
): Promise<ProjectOverviewResponse> {
  const params: Record<string, unknown> = {};
  if (options.scanId != null && options.scanId.length > 0) {
    params.scan_id = options.scanId;
  }
  const { data } = await api.get<ProjectOverviewResponse>(
    `/v1/projects/${projectId}/overview`,
    { params },
  );
  return data;
}

export async function listProjectComponents(
  projectId: string,
  params: ListComponentsParams = {},
): Promise<ComponentListResponse> {
  const { data } = await api.get<ComponentListResponse>(
    `/v1/projects/${projectId}/components`,
    {
      params: listComponentsQuery(params),
      // Repeat-key style for list parameters so FastAPI parses them as list[str].
      paramsSerializer: { indexes: null },
    },
  );
  return data;
}

export async function getComponent(
  componentId: string,
): Promise<ComponentDetailResponse> {
  const { data } = await api.get<ComponentDetailResponse>(
    `/v1/components/${componentId}`,
  );
  return data;
}

// ---------------------------------------------------------------------------
// Policy gate (build-blocking verdict) — v2.1 UI gap #1.
//
// Mirrors apps/backend/schemas/policy_gate.py::GateResultResponse 1:1. The
// gate is the build-blocking decision CI asks the portal to make, evaluated
// against the project's most recent successful scan. The Overview tab shows it
// next to the risk gauge so a developer can see the verdict + reason without
// opening a CI log.
// ---------------------------------------------------------------------------

export type GateOutcome = "pass" | "fail";

export interface GateResultResponse {
  /**
   * Overall outcome. `pass` when no open critical CVEs and no forbidden
   * licenses are present (and, when enabled, no findings at/above the EPSS
   * threshold), otherwise `fail`.
   */
  gate: GateOutcome;
  /** Human-readable explanation when `gate === "fail"`; `null` for passing builds. */
  reason: string | null;
  /** Open critical-severity findings on the evaluated scan. */
  critical_cve_count: number;
  /** Distinct component versions carrying a forbidden-classification license. */
  forbidden_license_count: number;
  /**
   * Open findings whose CVE EPSS score is at/above `epss_threshold`. Always 0
   * when the EPSS gate is disabled (`epss_threshold === null`).
   */
  epss_gate_count: number;
  /** Active EPSS gate threshold in [0, 1], or `null` when the EPSS gate is off. */
  epss_threshold: number | null;
  project_id: string;
  /**
   * The scan the verdict was computed against. `null` when the project has
   * never had a successful scan, in which case `gate === "pass"` by convention
   * (no signal = no block).
   */
  scan_id: string | null;
  /** Server timestamp at which the verdict was computed (UTC, ISO-8601). */
  evaluated_at: string;
}

export async function getGateResult(
  projectId: string,
  options: { scanId?: string } = {},
): Promise<GateResultResponse> {
  const params: Record<string, unknown> = {};
  if (options.scanId != null && options.scanId.length > 0) {
    params.scan_id = options.scanId;
  }
  const { data } = await api.get<GateResultResponse>(
    `/v1/projects/${projectId}/gate-result`,
    { params },
  );
  return data;
}
