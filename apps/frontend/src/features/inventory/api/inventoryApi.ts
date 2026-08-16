// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Organization-wide inventory wire layer (S2).
 *
 * Endpoints:
 *   GET /v1/inventory/components                          — the inventory page
 *   GET /v1/inventory/components/{id}/projects            — who uses a package
 *   GET /v1/inventory/vulnerabilities/{cve}/projects      — who a CVE reaches
 *
 * Wire types mirror `schemas/inventory.py` 1:1 (snake_case). No router import
 * here, no state — pure REST. The severity / license unions are re-exported
 * from the project-detail module rather than redeclared, so the two surfaces
 * cannot drift apart.
 */
import type {
  ComponentSeverity,
  LicenseCategoryName,
} from "@/features/projects/api/projectDetailApi";
import { api } from "@/lib/api";
import { downloadCsvExport } from "@/lib/csvExport";

export interface InventoryComponentRow {
  component_id: string;
  name: string;
  purl: string;
  package_type: string;
  /** Distinct non-archived projects whose current scan contains this package. */
  project_count: number;
  version_count: number;
  /** Up to five versions in use. A sample when `version_count` exceeds it. */
  versions: string[];
  severity_max: ComponentSeverity;
  /** Distinct CVEs across every in-use version — not a finding-row tally. */
  vulnerability_count: number;
  license_category_max: LicenseCategoryName;
  eol: boolean;
  outdated: boolean;
}

export interface InventoryComponentListResponse {
  items: InventoryComponentRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface InventoryProjectUsage {
  project_id: string;
  project_name: string;
  project_slug: string;
  version: string;
  direct: boolean;
  scan_id: string;
  scanned_at: string;
}

export interface InventoryProjectUsageListResponse {
  items: InventoryProjectUsage[];
  total: number;
  limit: number;
  offset: number;
}

export interface InventoryVulnerabilityImpact {
  project_id: string;
  project_name: string;
  project_slug: string;
  component_name: string;
  purl: string;
  version: string;
  finding_id: string;
  status: string;
  severity: ComponentSeverity;
}

export interface InventoryVulnerabilityImpactResponse {
  external_id: string;
  severity: ComponentSeverity;
  items: InventoryVulnerabilityImpact[];
  total: number;
  limit: number;
  offset: number;
}

export type InventorySortKey =
  | "name"
  | "project_count"
  | "severity"
  | "license";
export type SortOrder = "asc" | "desc";

export interface ListInventoryParams {
  limit?: number;
  offset?: number;
  /** Substring match on package name or purl. */
  q?: string;
  packageType?: string[];
  severity?: ComponentSeverity[];
  licenseCategory?: LicenseCategoryName[];
  /** Tri-state: omit for "any", true/false to require it. */
  eol?: boolean;
  outdated?: boolean;
  sort?: InventorySortKey;
  order?: SortOrder;
}

/**
 * Build the query object, omitting anything the backend should treat as
 * "unset". Empty arrays and empty strings are dropped rather than sent as
 * empty parameters, which the backend reads as "filter to nothing".
 */
function buildQuery(params: ListInventoryParams): Record<string, unknown> {
  const query: Record<string, unknown> = {};
  if (params.limit != null) query.limit = params.limit;
  if (params.offset != null) query.offset = params.offset;
  if (params.q && params.q.length > 0) query.q = params.q;
  if (params.packageType?.length) query.package_type = params.packageType;
  if (params.severity?.length) query.severity = params.severity;
  if (params.licenseCategory?.length)
    query.license_category = params.licenseCategory;
  if (params.eol === true || params.eol === false) query.eol = params.eol;
  if (params.outdated === true || params.outdated === false)
    query.outdated = params.outdated;
  if (params.sort) query.sort = params.sort;
  if (params.order) query.order = params.order;
  return query;
}

export async function listInventoryComponents(
  params: ListInventoryParams = {},
): Promise<InventoryComponentListResponse> {
  const { data } = await api.get<InventoryComponentListResponse>(
    "/v1/inventory/components",
    {
      params: buildQuery(params),
      // FastAPI reads `list[str]` as repeated keys, not `key[0]=`.
      paramsSerializer: { indexes: null },
    },
  );
  return data;
}

/**
 * Download the filtered inventory as CSV (B5).
 *
 * Same params, same serialiser as the list, so the file cannot carry rows
 * the screen was filtering out.
 */
export async function exportInventoryComponentsCsv(
  params: ListInventoryParams = {},
): Promise<void> {
  const { limit: _limit, offset: _offset, ...filters } = params;
  await downloadCsvExport(
    "/v1/inventory/components/export.csv",
    buildQuery(filters),
    "inventory.csv",
  );
}

export async function listComponentUsage(
  componentId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<InventoryProjectUsageListResponse> {
  const { data } = await api.get<InventoryProjectUsageListResponse>(
    `/v1/inventory/components/${componentId}/projects`,
    { params },
  );
  return data;
}

export async function listVulnerabilityImpact(
  externalId: string,
  params: { limit?: number; offset?: number } = {},
): Promise<InventoryVulnerabilityImpactResponse> {
  const { data } = await api.get<InventoryVulnerabilityImpactResponse>(
    `/v1/inventory/vulnerabilities/${encodeURIComponent(externalId)}/projects`,
    { params },
  );
  return data;
}
