/**
 * Compliance unified-grid wire surface — W9-#58.
 *
 * Backs the redesigned Compliance tab with a single endpoint:
 *
 *   - GET /v1/projects/{id}/compliance → ComplianceListResponse
 *
 * The grid row's drawer is still served by the existing
 * ``GET /v1/license_findings/{id}`` (License drawer) and
 * ``GET /v1/projects/{id}/obligations/{id}`` (Obligation drawer) — there is
 * no new drawer endpoint. ``license_finding_id`` on each row is the same
 * opaque handle the License drawer already accepts.
 *
 * Wire types mirror `apps/backend/schemas/compliance.py` 1:1 (snake_case).
 *
 * Hard rules (CLAUDE.md):
 *   - All 4xx/5xx responses are `application/problem+json` and surface as
 *     {@link ProblemError} via the shared `api` interceptor.
 *   - No router import here. No state — pure REST.
 */
import { api } from "@/lib/api";

import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";

export type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";

export type ComplianceSortKey =
  | "category"
  | "license_name"
  | "spdx_id"
  | "affected_count";
export type SortOrder = "asc" | "desc";

/** ORT classification kind on the representative license finding. */
export type LicenseFindingKind = "declared" | "concluded" | "detected";

export interface ComplianceAffectedComponent {
  component_version_id: string;
  name: string;
  version: string;
  /** Package URL including version. Null when the scan did not record one. */
  purl: string | null;
}

export interface ComplianceObligation {
  obligation_id: string;
  kind: string;
  /** Short one-line summary (capped at 240 chars server-side). */
  summary: string;
}

export interface ComplianceRow {
  /**
   * license_findings.id of a representative finding for this license — the
   * same handle the existing License drawer accepts.
   */
  license_finding_id: string;
  license_id: string;
  spdx_id: string | null;
  license_name: string;
  category: LicenseCategoryName;
  category_source: string;
  kind: LicenseFindingKind;
  affected_component_count: number;
  affected_components: ComplianceAffectedComponent[];
  obligations: ComplianceObligation[];
  notice_required: boolean;
  category_override_source: string | null;
}

export interface ComplianceDistribution {
  forbidden: number;
  conditional: number;
  allowed: number;
  unknown: number;
}

export interface ComplianceListResponse {
  items: ComplianceRow[];
  distribution: ComplianceDistribution;
  total: number;
  limit: number;
  offset: number;
  generated_at: string;
}

// ---------------------------------------------------------------------------
// List parameters
// ---------------------------------------------------------------------------

export interface ListComplianceParams {
  limit?: number;
  offset?: number;
  search?: string;
  categories?: LicenseCategoryName[];
  /** Obligation-kind filter — repeated key serialization. */
  kinds?: string[];
  /** Restrict to rows whose license carries (or does not carry) obligations. */
  has_obligations?: boolean;
  sort?: ComplianceSortKey;
  order?: SortOrder;
  /**
   * Pin the read to a specific succeeded scan (feature #28 snapshot anchoring).
   * Omit → the project's latest succeeded scan (default).
   */
  scanId?: string;
}

function listComplianceQuery(
  params: ListComplianceParams,
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
  if (params.has_obligations != null) {
    out.has_obligations = params.has_obligations;
  }
  if (params.sort != null) out.sort = params.sort;
  if (params.order != null) out.order = params.order;
  if (params.scanId != null && params.scanId.length > 0) {
    out.scan_id = params.scanId;
  }
  return out;
}

export async function listProjectCompliance(
  projectId: string,
  params: ListComplianceParams = {},
): Promise<ComplianceListResponse> {
  const { data } = await api.get<ComplianceListResponse>(
    `/v1/projects/${projectId}/compliance`,
    {
      params: listComplianceQuery(params),
      // Repeat-key style for list params so FastAPI parses them as list[str].
      paramsSerializer: { indexes: null },
    },
  );
  return data;
}
