/**
 * Report-history wire surface — W3 #32 (Reports center, FE half).
 *
 * One read endpoint backs the Reports tab's right-hand activity table:
 *
 *   - GET /v1/projects/{id}/reports/history → ReportHistoryResponse
 *
 * Wire types mirror `apps/backend/schemas/report_download.py` 1:1 (snake_case)
 * so a future bump to the BE schema surfaces here as a typecheck failure rather
 * than a silent runtime drift.
 *
 * Design notes
 * ------------
 * - The router accepts the `type` filter as a repeated query parameter
 *   (``?type=notice&type=sbom``). axios serializes arrays as `?type[]=…` by
 *   default which the FastAPI Query() gate rejects — pass
 *   `paramsSerializer: { indexes: null }` to emit one bare `type=` per value,
 *   exactly like the obligations list call.
 * - `client_ip` / `user_agent` deliberately do NOT appear on the wire (BE keeps
 *   them out of the response per CLAUDE.md §5 PII rule). The shape here makes
 *   that explicit — the SPA cannot accidentally display them.
 * - 404 is the cross-team existence-hide envelope. The shared problem handler
 *   surfaces `ProblemError`; the tab maps it to a generic "Reports unavailable"
 *   string so the UI does not leak permission semantics.
 */
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Wire types — mirror apps/backend/schemas/report_download.py
// ---------------------------------------------------------------------------

/**
 * Closed enum — mirrors the Postgres ``report_type_enum``. Frozen contract:
 * the four download surfaces (NOTICE / SBOM / Vulnerability PDF / VEX export)
 * each emit exactly one of these tokens at write time.
 */
export type ReportType = "notice" | "sbom" | "vuln_pdf" | "vex_export";

/** Canonical ordering for the type filter dropdown + history row badges. */
export const REPORT_TYPES: readonly ReportType[] = [
  "notice",
  "sbom",
  "vuln_pdf",
  "vex_export",
] as const;

export interface ReportDownloadUserSummary {
  id: string;
  email: string;
}

export interface ReportDownloadEntry {
  id: string;
  project_id: string;
  /**
   * NULL for VEX export rows by design (VEX summarises the current state and is
   * not scan-bound), and NULL when the originating scan was later pruned (FK is
   * ``ON DELETE SET NULL``).
   */
  scan_id: string | null;
  /** Denormalised tenant pointer mirrored from the parent project at emit time. */
  team_id: string;
  /**
   * The actor who triggered the download. NULL when the user account was
   * deleted — the history fact survives the actor.
   */
  user: ReportDownloadUserSummary | null;
  report_type: ReportType;
  /**
   * Free token (cyclonedx-json / spdx-tv / pdf / text / cdx-vex / …). Not an
   * enum because new export formats appear on the timescale of feature work.
   */
  format: string;
  /** Body length in bytes when known at emit time, else NULL. */
  size_bytes: number | null;
  created_at: string;
}

export interface ReportHistoryResponse {
  items: ReportDownloadEntry[];
  total: number;
  page: number;
  page_size: number;
}

// ---------------------------------------------------------------------------
// List parameters + wire call
// ---------------------------------------------------------------------------

export interface ListReportHistoryParams {
  /** Optional multi-select report-type filter. Empty / undefined → all four. */
  types?: ReportType[];
  /** Optional scan id (UUID string) filter. */
  scanId?: string;
  /** 1-based page number; defaults server-side to 1. */
  page?: number;
  /** Rows per page (1..200); defaults server-side to 50. */
  pageSize?: number;
}

function listReportHistoryQuery(
  params: ListReportHistoryParams,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (params.types && params.types.length > 0) {
    // axios + `indexes: null` serialises arrays as `?type=a&type=b` — the
    // exact shape FastAPI's `list[ReportType]` Query() expects.
    out.type = params.types;
  }
  if (params.scanId != null && params.scanId.length > 0) {
    out.scan_id = params.scanId;
  }
  if (params.page != null) out.page = params.page;
  if (params.pageSize != null) out.page_size = params.pageSize;
  return out;
}

export async function fetchReportHistory(
  projectId: string,
  params: ListReportHistoryParams = {},
): Promise<ReportHistoryResponse> {
  const { data } = await api.get<ReportHistoryResponse>(
    `/v1/projects/${projectId}/reports/history`,
    {
      params: listReportHistoryQuery(params),
      paramsSerializer: { indexes: null },
    },
  );
  return data;
}
