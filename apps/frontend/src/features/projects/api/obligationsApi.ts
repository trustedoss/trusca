/**
 * Obligations wire surface — Phase 3 PR #13.
 *
 * Three read-only endpoints back the project Obligations tab + drawer:
 *
 *   - GET /v1/projects/{id}/obligations                   → ObligationListResponse
 *   - GET /v1/projects/{id}/obligations/{obligation_id}   → ObligationDetailResponse
 *   - GET /v1/projects/{id}/notice                        → text/plain or text/markdown
 *
 * Wire types mirror `apps/backend/schemas/obligation_detail.py` 1:1 (snake_case).
 *
 * Read-only domain: obligations are a per-license catalog, no analyst workflow,
 * no PATCH counterpart.
 *
 * Hard rules (CLAUDE.md):
 *   - All 4xx/5xx responses are `application/problem+json` and surface as
 *     {@link ProblemError} via the shared `api` interceptor.
 *   - The NOTICE endpoint returns raw text — we pass `responseType: "text"`
 *     so axios doesn't try to JSON-parse it.
 */
import { api } from "@/lib/api";

import type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";

// ---------------------------------------------------------------------------
// Wire types — mirror apps/backend/schemas/obligation_detail.py
// ---------------------------------------------------------------------------

export type { LicenseCategoryName } from "@/features/projects/api/projectDetailApi";

export type ObligationSortKey =
  | "category"
  | "license_name"
  | "kind"
  | "affected_count";
export type SortOrder = "asc" | "desc";
export type NoticeFormat = "text" | "markdown" | "html";

/**
 * Ranked allow-list of obligation kinds the backend advertises in the
 * distribution payload's canonical order. The DB column is open, so unknown
 * kinds round-trip transparently — they just sort after these.
 *
 * Mirror of `apps/backend/schemas/obligation_detail.py::KNOWN_OBLIGATION_KINDS`
 * (same order). PR-6 FE regression guards: pinned by
 * `tests/unit/contracts/catalogMirrors.test.ts` — H-9 added `patent` to the
 * backend vocabulary while this list silently stayed at seven, so the kind
 * filter and ranked chip ordering treated a real, emitted obligation as
 * "unknown".
 */
export const KNOWN_OBLIGATION_KINDS = [
  "attribution",
  "notice",
  "source-disclosure",
  "copyleft",
  "modifications",
  "dynamic-linking",
  "no-endorsement",
  "patent",
] as const;
export type KnownObligationKind = (typeof KNOWN_OBLIGATION_KINDS)[number];

export interface ObligationListItem {
  id: string;
  license_id: string;
  license_spdx_id: string | null;
  license_name: string;
  license_category: LicenseCategoryName;
  kind: string;
  text: string;
  link: string | null;
  affected_count: number;
  updated_at: string;
}

export interface ObligationListResponse {
  items: ObligationListItem[];
  /** kind → count, ordered with known kinds first (insertion order is contract). */
  distribution: Record<string, number>;
  total: number;
}

export interface AffectedComponentByObligation {
  component_version_id: string;
  component_name: string;
  version: string;
}

export interface ObligationDetailResponse {
  id: string;
  license_id: string;
  license_spdx_id: string | null;
  license_name: string;
  license_category: LicenseCategoryName;
  license_reference_url: string | null;
  kind: string;
  text: string;
  /** True when the server clamped `text` at 64 KiB (chore PR #3). */
  text_truncated: boolean;
  link: string | null;
  affected_components: AffectedComponentByObligation[];
  /** True when `affected_components` was capped at 500 rows (chore PR #3). */
  affected_components_truncated: boolean;
  /** Pre-cap row count used by the UI to render "X of N" disclosure. */
  affected_components_total: number;
  created_at: string;
  updated_at: string;
}

export interface NoticeMetadata {
  generatedAt: string | null;
  licenseCount: number | null;
  obligationCount: number | null;
}

export interface NoticeResult {
  body: string;
  format: NoticeFormat;
  metadata: NoticeMetadata;
}

// ---------------------------------------------------------------------------
// List parameters
// ---------------------------------------------------------------------------

export interface ListObligationsParams {
  limit?: number;
  offset?: number;
  search?: string;
  kinds?: string[];
  categories?: LicenseCategoryName[];
  sort?: ObligationSortKey;
  order?: SortOrder;
  /**
   * Pin the read to a specific succeeded scan (feature #28 snapshot anchoring).
   * Omit → the project's latest succeeded scan (unchanged default).
   */
  scanId?: string;
}

function listObligationsQuery(
  params: ListObligationsParams,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (params.limit != null) out.limit = params.limit;
  if (params.offset != null) out.offset = params.offset;
  if (params.search != null && params.search.length > 0) {
    out.search = params.search;
  }
  if (params.kinds && params.kinds.length > 0) {
    out.kind = params.kinds;
  }
  if (params.categories && params.categories.length > 0) {
    out.category = params.categories;
  }
  if (params.sort != null) out.sort = params.sort;
  if (params.order != null) out.order = params.order;
  if (params.scanId != null && params.scanId.length > 0) {
    out.scan_id = params.scanId;
  }
  return out;
}

export async function listProjectObligations(
  projectId: string,
  params: ListObligationsParams = {},
): Promise<ObligationListResponse> {
  const { data } = await api.get<ObligationListResponse>(
    `/v1/projects/${projectId}/obligations`,
    {
      params: listObligationsQuery(params),
      paramsSerializer: { indexes: null },
    },
  );
  return data;
}

export async function getObligation(
  projectId: string,
  obligationId: string,
): Promise<ObligationDetailResponse> {
  const { data } = await api.get<ObligationDetailResponse>(
    `/v1/projects/${projectId}/obligations/${obligationId}`,
  );
  return data;
}

export interface FetchNoticeParams {
  format?: NoticeFormat;
  /** When true, ask the backend to set Content-Disposition: attachment. */
  download?: boolean;
}

export async function fetchProjectNotice(
  projectId: string,
  params: FetchNoticeParams = {},
): Promise<NoticeResult> {
  const fmt: NoticeFormat = params.format ?? "text";
  const response = await api.get<string>(`/v1/projects/${projectId}/notice`, {
    params: {
      format: fmt,
      ...(params.download ? { download: true } : {}),
    },
    responseType: "text",
    transformResponse: [(raw: string) => raw],
  });
  const headers = response.headers ?? {};
  const headerValue = (key: string): string | null => {
    if (typeof (headers as { get?: (k: string) => string | null }).get === "function") {
      return (headers as { get: (k: string) => string | null }).get(key);
    }
    const record = headers as Record<string, unknown>;
    const v = record[key] ?? record[key.toLowerCase()];
    return typeof v === "string" ? v : null;
  };
  const licenseCountRaw = headerValue("X-Notice-License-Count");
  const obligationCountRaw = headerValue("X-Notice-Obligation-Count");
  const generatedAt = headerValue("X-Notice-Generated-At");
  return {
    body: typeof response.data === "string" ? response.data : String(response.data ?? ""),
    format: fmt,
    metadata: {
      generatedAt,
      licenseCount: licenseCountRaw == null ? null : Number.parseInt(licenseCountRaw, 10),
      obligationCount:
        obligationCountRaw == null ? null : Number.parseInt(obligationCountRaw, 10),
    },
  };
}
