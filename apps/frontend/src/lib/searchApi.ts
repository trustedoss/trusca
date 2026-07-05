/**
 * Global cross-project search — BomLens parity backlog Phase H-2.
 *
 * Thin typed wrapper around the shared `api` axios instance for the global
 * ⌘K palette. Kept free of TanStack Query so the same call can be used from a
 * hook, a mutation, or an imperative path (mirrors `projectsApi.ts`).
 *
 * Backend contract — `GET /v1/search`:
 *   query params:
 *     - `q`     : search string (backend requires ≥ 2 chars; caller gates too).
 *     - `kinds` : comma-joined subset of {"components","vulnerabilities"}.
 *                 Omitted → backend returns every kind.
 *   The response is already team-scoped by the backend — only hits from
 *   projects the caller can reach come back, at most 20 per category.
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Wire types — snake_case, mirror the backend `GET /v1/search` response.
// ---------------------------------------------------------------------------

/** The searchable entity kinds the global palette can request. */
export const SEARCH_KINDS = ["components", "vulnerabilities"] as const;

export type SearchKind = (typeof SEARCH_KINDS)[number];

/** A single component match, carrying its owning project for deep-linking. */
export interface SearchComponentHit {
  project_id: string;
  project_name: string;
  project_slug: string;
  component_name: string;
  version: string;
  purl: string;
}

/** A single CVE match, carrying its owning project for deep-linking. */
export interface SearchVulnerabilityHit {
  project_id: string;
  project_name: string;
  project_slug: string;
  cve_id: string;
  severity: string;
}

export interface GlobalSearchResults {
  query: string;
  components: SearchComponentHit[];
  vulnerabilities: SearchVulnerabilityHit[];
}

// ---------------------------------------------------------------------------
// Endpoint
// ---------------------------------------------------------------------------

/**
 * Cross-project component/CVE search for the ⌘K palette.
 *
 * @param q      The search string. Callers gate on `q.length >= 2` — the
 *               backend rejects shorter queries, so we never send them.
 * @param kinds  Restrict the categories fetched. Defaults to both. Joined into
 *               the `kinds` query param as a comma-separated list.
 */
export async function globalSearch(
  q: string,
  kinds: readonly SearchKind[] = SEARCH_KINDS,
  config?: AxiosRequestConfig,
): Promise<GlobalSearchResults> {
  const { data } = await api.get<GlobalSearchResults>("/v1/search", {
    ...config,
    params: {
      q,
      kinds: kinds.length > 0 ? kinds.join(",") : undefined,
    },
  });
  return data;
}
