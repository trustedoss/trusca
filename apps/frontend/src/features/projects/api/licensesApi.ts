/**
 * Licenses wire surface — Phase 3 PR #12.
 *
 * Two read-only endpoints back the project Licenses tab + drawer:
 *
 *   - GET /v1/projects/{id}/licenses        → LicenseListResponse
 *   - GET /v1/license_findings/{finding_id} → LicenseDetailResponse
 *
 * The wire types mirror `apps/backend/schemas/license_detail.py` 1:1
 * (snake_case). Hooks in `./useLicenses.ts` and `./useLicenseFinding.ts`
 * wrap these in TanStack Query.
 *
 * Read-only domain: license findings carry no analyst workflow (no status
 * transitions, no audit log). ORT's ruleset is the authoritative classifier
 * — categories and kinds are produced by the scan pipeline and immutable.
 * No PATCH counterpart in this PR.
 *
 * Hard rules (CLAUDE.md):
 *   - All four/5xx responses are `application/problem+json` and surface as
 *     {@link ProblemError} via the shared `api` interceptor.
 *   - No router import here. No state — pure REST.
 */
import { api } from "@/lib/api";

import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";

// ---------------------------------------------------------------------------
// Wire types — mirror apps/backend/schemas/license_detail.py
// ---------------------------------------------------------------------------

/** Re-export for convenience so license-feature callers don't reach into projectDetailApi. */
export type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";

/** ORT classification kind on a single finding row. */
export type LicenseFindingKind = "declared" | "concluded" | "detected";

export type LicenseSortKey = "category" | "name" | "spdx_id" | "affected_count";
export type SortOrder = "asc" | "desc";

export interface LicenseListItem {
  /** license_findings.id of a representative finding (drawer primary key). */
  id: string;
  license_id: string;
  /** SPDX short id (e.g. MIT, Apache-2.0). Null for ORT custom licenses. */
  spdx_id: string | null;
  name: string;
  category: LicenseCategoryName;
  kind: LicenseFindingKind;
  /** Distinct component_versions in the latest scan that carry this license. */
  affected_count: number;
  is_osi_approved: boolean;
  is_fsf_libre: boolean;
  /** Today echoes `id`; kept distinct so frontends stay forward-compatible. */
  sample_finding_id: string;
}

export interface LicenseDistribution {
  forbidden: number;
  conditional: number;
  allowed: number;
  unknown: number;
}

export interface LicenseListResponse {
  items: LicenseListItem[];
  distribution: LicenseDistribution;
  total: number;
}

export interface AffectedComponentByLicense {
  component_version_id: string;
  component_name: string;
  version: string;
  kind: LicenseFindingKind;
  source_path: string | null;
}

export interface LicenseDetailResponse {
  /** license_findings.id (the row the URL points at). */
  id: string;
  license_id: string;
  spdx_id: string | null;
  name: string;
  category: LicenseCategoryName;
  is_osi_approved: boolean;
  is_fsf_libre: boolean;
  is_deprecated_license_id: boolean;
  reference_url: string | null;
  finding_kind: LicenseFindingKind;
  /**
   * Best-effort pass-through of license_findings.raw_data. ORT may include
   * matched-text excerpts, license-detector confidence, copyright statements,
   * etc. Frontends MUST render this defensively (no `dangerouslySetInnerHTML`).
   * `null` when the scan pipeline did not emit any raw data.
   */
  ort_match: Record<string, unknown> | null;
  affected_components: AffectedComponentByLicense[];
  /** True when `affected_components` was capped at 500 rows (chore PR #3). */
  affected_components_truncated: boolean;
  /** Pre-cap row count used by the UI to render "X of N" disclosure. */
  affected_components_total: number;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// List parameters
// ---------------------------------------------------------------------------

export interface ListLicensesParams {
  limit?: number;
  offset?: number;
  search?: string;
  categories?: LicenseCategoryName[];
  kinds?: LicenseFindingKind[];
  sort?: LicenseSortKey;
  order?: SortOrder;
  /**
   * Pin the read to a specific succeeded scan (feature #28 snapshot anchoring).
   * Omit → the project's latest succeeded scan (unchanged default).
   */
  scanId?: string;
}

/**
 * Build the query-string params object axios accepts. List parameters
 * (`category`, `kind`) serialize to repeated keys (`?category=allowed&category=forbidden`)
 * so FastAPI parses them as `list[str]` (PR #10 / #11 convention).
 *
 * The backend query parameter names are singular (`category`, `kind`) even
 * though the client surface uses plural (`categories`, `kinds`); we map
 * here so callers don't have to.
 */
function listLicensesQuery(
  params: ListLicensesParams,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (params.limit != null) out.limit = params.limit;
  if (params.offset != null) out.offset = params.offset;
  if (params.search != null && params.search.length > 0) {
    out.search = params.search;
  }
  if (params.categories && params.categories.length > 0) {
    out.category = params.categories;
  }
  if (params.kinds && params.kinds.length > 0) {
    out.kind = params.kinds;
  }
  if (params.sort != null) out.sort = params.sort;
  if (params.order != null) out.order = params.order;
  if (params.scanId != null && params.scanId.length > 0) {
    out.scan_id = params.scanId;
  }
  return out;
}

export async function listProjectLicenses(
  projectId: string,
  params: ListLicensesParams = {},
): Promise<LicenseListResponse> {
  const { data } = await api.get<LicenseListResponse>(
    `/v1/projects/${projectId}/licenses`,
    {
      params: listLicensesQuery(params),
      // Repeat-key style for list params so FastAPI parses them as list[str].
      paramsSerializer: { indexes: null },
    },
  );
  return data;
}

export async function getLicenseFinding(
  findingId: string,
): Promise<LicenseDetailResponse> {
  const { data } = await api.get<LicenseDetailResponse>(
    `/v1/license_findings/${findingId}`,
  );
  return data;
}
