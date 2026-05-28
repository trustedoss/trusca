/**
 * Project release-diff wire surface — feature #28 Phase 2 (compare view).
 *
 * One endpoint backs the "Compare to" screen:
 *
 *   - GET /v1/projects/{id}/diff?base=<scan_id>&target=<scan_id> → ProjectDiff
 *
 * Both `base` and `target` are required succeeded-scan ids of the project. An
 * invalid / cross-project / non-succeeded id is a 404 `application/problem+json`
 * on the wire (surfaced as {@link ProblemError} via the shared `api`
 * interceptor). `base === target` is legal and returns an all-empty diff so the
 * UI can render a friendly "no differences" state without a special case.
 *
 * The wire types mirror the backend `ProjectDiff` contract 1:1 (snake_case).
 * Most lists may be empty; `truncated` is `true` when any list was capped at the
 * server limit (1000) — the UI surfaces a muted hint rather than fabricating
 * completeness.
 *
 * Hard rules (CLAUDE.md):
 *   - No router import here. No state — pure REST.
 *   - All 4xx/5xx responses are problem+json and surface as {@link ProblemError}.
 */
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Wire types — mirror the backend ProjectDiff contract
// ---------------------------------------------------------------------------

/** Pass/fail verdict of the build gate evaluated against a snapshot's scan. */
export type DiffGateStatus = "pass" | "fail";

/** Severity buckets the summary tracks, in display priority order. */
export type DiffSeverityBucket = "critical" | "high" | "medium" | "low";

/** License categories the delta table tracks, in display priority order. */
export type DiffLicenseCategory =
  | "prohibited"
  | "conditional"
  | "permissive"
  | "unknown";

/** The base / target endpoint describing one side of the comparison. */
export interface DiffEndpoint {
  scan_id: string;
  /** Optional release/version label; frequently `null`. */
  release: string | null;
  /** ISO-8601 instant the scan was created. */
  created_at: string;
}

/** A `{ base, target }` pair of a scalar value across the two snapshots. */
export interface DiffValuePair<T> {
  base: T;
  target: T;
}

export interface DiffSummary {
  risk_score: DiffValuePair<number | null>;
  severity: Record<DiffSeverityBucket, DiffValuePair<number>>;
  gate: DiffValuePair<DiffGateStatus | null>;
  component_count: DiffValuePair<number>;
}

/** A component present in exactly one snapshot (added → target / removed → base). */
export interface DiffComponent {
  name: string;
  namespace: string | null;
  purl: string;
  version: string;
}

/** A component present in both snapshots at different versions. */
export interface DiffComponentChange {
  name: string;
  namespace: string | null;
  purl: string;
  base_version: string;
  target_version: string;
}

export interface DiffComponents {
  added: DiffComponent[];
  removed: DiffComponent[];
  changed: DiffComponentChange[];
}

/** A vulnerability finding gained (introduced) or lost (resolved) target↔base. */
export interface DiffVulnerability {
  cve_id: string;
  severity: string;
  component_name: string;
  component_version: string;
}

export interface DiffVulnerabilities {
  introduced: DiffVulnerability[];
  resolved: DiffVulnerability[];
}

export interface DiffLicenses {
  category_delta: Record<DiffLicenseCategory, DiffValuePair<number>>;
}

export interface ProjectDiff {
  base: DiffEndpoint;
  target: DiffEndpoint;
  summary: DiffSummary;
  components: DiffComponents;
  vulnerabilities: DiffVulnerabilities;
  licenses: DiffLicenses;
  /** `true` when any list was capped at the server limit (1000). */
  truncated: boolean;
}

// ---------------------------------------------------------------------------
// Endpoint
// ---------------------------------------------------------------------------

export interface GetProjectDiffParams {
  /** Base (left) succeeded-scan id — the "before" snapshot. */
  base: string;
  /** Target (right) succeeded-scan id — the "after" snapshot. */
  target: string;
}

export async function getProjectDiff(
  projectId: string,
  params: GetProjectDiffParams,
): Promise<ProjectDiff> {
  const { data } = await api.get<ProjectDiff>(
    `/v1/projects/${projectId}/diff`,
    { params: { base: params.base, target: params.target } },
  );
  return data;
}
