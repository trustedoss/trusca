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

export interface ScanSummary {
  id: string;
  kind: string;
  status: string;
  progress_percent: number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface ProjectOverviewResponse {
  project_id: string;
  project_name: string;
  total_components: number;
  /** Counts per severity bucket. May omit zero buckets — render with fallback. */
  severity_distribution: Partial<Record<ComponentSeverity, number>>;
  /** Counts per license category. */
  license_distribution: Partial<Record<LicenseCategoryName, number>>;
  risk_score: number;
  recent_scans: ScanSummary[];
  last_scan_at: string | null;
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
  title: string;
  description: string | null;
  fixed_version: string | null;
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
  raw_data: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export type ComponentSortKey = "name" | "severity" | "license";
export type SortOrder = "asc" | "desc";

export interface ListComponentsParams {
  limit?: number;
  offset?: number;
  search?: string;
  severity?: ComponentSeverity[];
  license_category?: LicenseCategoryName[];
  sort?: ComponentSortKey;
  order?: SortOrder;
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
  return out;
}

export async function getProjectOverview(
  projectId: string,
): Promise<ProjectOverviewResponse> {
  const { data } = await api.get<ProjectOverviewResponse>(
    `/v1/projects/${projectId}/overview`,
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
