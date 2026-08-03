// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Wire layer for the full search page (S3).
 *
 *   GET  /v1/search/results   — one kind at a time, paged, faceted
 *   GET  /v1/saved-searches   — the caller's bookmarks
 *   POST /v1/saved-searches
 *   DELETE /v1/saved-searches/{id}
 *
 * Deliberately NOT `lib/searchApi.ts`. That module talks to `/v1/search`, the
 * ⌘K palette's endpoint, whose response shape is pinned by its own tests. The
 * page needs paging and facets; the palette needs neither. Two clients, two
 * contracts.
 */
import { api } from "@/lib/api";

export const SEARCH_KINDS = [
  "projects",
  "components",
  "vulnerabilities",
  "licenses",
] as const;
export type SearchKind = (typeof SEARCH_KINDS)[number];

export interface SearchFacetBucket {
  value: string;
  count: number;
}

export interface ProjectResult {
  project_id: string;
  project_name: string;
  project_slug: string;
  git_url: string | null;
  archived: boolean;
}

export interface ComponentResult {
  project_id: string;
  project_name: string;
  project_slug: string;
  component_id: string;
  component_name: string;
  version: string;
  purl: string;
  package_type: string;
}

export interface VulnerabilityResult {
  project_id: string;
  project_name: string;
  project_slug: string;
  finding_id: string;
  cve_id: string;
  severity: string;
  status: string;
  component_name: string;
  version: string;
}

export interface LicenseResult {
  project_id: string;
  project_name: string;
  project_slug: string;
  license_id: string;
  spdx_id: string | null;
  license_name: string;
  category: string;
  component_name: string;
  version: string;
}

/**
 * One page of results.
 *
 * Exactly one item list is populated — the one `kind` names. Four typed lists
 * rather than one heterogeneous list keeps both sides checkable; the active
 * tab reads its own and ignores the rest.
 */
export interface SearchResultsPage {
  kind: SearchKind;
  query: string;
  items_projects: ProjectResult[];
  items_components: ComponentResult[];
  items_vulnerabilities: VulnerabilityResult[];
  items_licenses: LicenseResult[];
  total: number;
  page: number;
  size: number;
  facets: Record<string, SearchFacetBucket[]>;
}

export interface SearchResultsParams {
  kind: SearchKind;
  q: string;
  page?: number;
  size?: number;
  severity?: string[];
  status?: string[];
  packageType?: string[];
  licenseCategory?: string[];
}

export async function fetchSearchResults(
  params: SearchResultsParams,
): Promise<SearchResultsPage> {
  const query: Record<string, unknown> = {
    kind: params.kind,
    q: params.q,
  };
  if (params.page != null) query.page = params.page;
  if (params.size != null) query.size = params.size;
  if (params.severity?.length) query.severity = params.severity;
  if (params.status?.length) query.status = params.status;
  if (params.packageType?.length) query.package_type = params.packageType;
  if (params.licenseCategory?.length)
    query.license_category = params.licenseCategory;

  const { data } = await api.get<SearchResultsPage>("/v1/search/results", {
    params: query,
    // FastAPI reads `list[str]` as repeated keys, not `key[0]=`.
    paramsSerializer: { indexes: null },
  });
  return data;
}

export interface SavedSearch {
  id: string;
  name: string;
  kind: SearchKind;
  /** The saved query string, replayed verbatim. Opaque to the server. */
  params: Record<string, unknown>;
  created_at: string;
}

export interface SavedSearchListResponse {
  items: SavedSearch[];
  total: number;
  /** The per-user ceiling, so the UI can disable Save before the request fails. */
  limit: number;
}

export async function listSavedSearches(): Promise<SavedSearchListResponse> {
  const { data } = await api.get<SavedSearchListResponse>("/v1/saved-searches");
  return data;
}

export async function createSavedSearch(input: {
  name: string;
  kind: SearchKind;
  params: Record<string, unknown>;
}): Promise<SavedSearch> {
  const { data } = await api.post<SavedSearch>("/v1/saved-searches", input);
  return data;
}

export async function deleteSavedSearch(id: string): Promise<void> {
  await api.delete(`/v1/saved-searches/${id}`);
}
