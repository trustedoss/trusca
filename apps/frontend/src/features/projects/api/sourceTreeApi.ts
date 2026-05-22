/**
 * Source-tree viewer wire surface — G3.3.
 *
 * Two read-only endpoints back the project Source tab (Protex-style file-tree
 * viewer). Both run through the shared `api` axios instance so the bearer token
 * rides the `Authorization` header (NOT on the URL/history) and every non-2xx
 * surfaces as a {@link ProblemError} via the response interceptor — same as the
 * obligations / vuln-report wire layers.
 *
 *   - GET /v1/projects/{id}/source-tree?path=&page=&size=&scan_id=
 *         → {@link SourceTreePage} — immediate children of `path` (lazy per-dir).
 *   - GET /v1/projects/{id}/source-file?path=&scan_id=
 *         → {@link SourceFileResponse} — one file's bytes (capped) + per-line
 *           license matches.
 *
 * Wire types mirror `apps/backend/schemas/source_tree.py` 1:1 (snake_case).
 *
 * 404 semantics: old scans have no preserved source (the per-scan tarball was
 * introduced in G3.1). The backend returns a 404 in that case which the hooks
 * surface as an EMPTY state ("re-scan to enable"), not an error toast.
 */
import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Wire types — mirror apps/backend/schemas/source_tree.py
// ---------------------------------------------------------------------------

/** 'utf-8' files carry decoded `content`; 'binary' files carry no content. */
export type FileEncoding = "utf-8" | "binary";

export interface SourceTreeEntry {
  /** Base name of the entry (no path separators). */
  name: string;
  /** POSIX path relative to the source root. */
  path: string;
  /** True for a directory, false for a regular file. */
  is_dir: boolean;
  /** Uncompressed file size in bytes; 0 for directories. */
  byte_size: number;
  /** Distinct SPDX ids recorded for this exact path (cheap per-file badges). */
  license_spdx_ids: string[];
}

export interface SourceTreePage {
  /** The scan whose preserved source this tree was read from. */
  scan_id: string;
  /** The directory whose children are listed. Empty string = source root. */
  path: string;
  /** Immediate children of `path` on this page (dirs first). */
  entries: SourceTreeEntry[];
  /** Total immediate children in `path` across all pages. */
  total: number;
  /** 1-based page index. */
  page: number;
  /** Page size used for this response. */
  size: number;
}

export interface LicenseMatch {
  /** SPDX identifier of the matched license. */
  spdx_id: string;
  /** 1-based first line of the match (inclusive). */
  start_line: number;
  /** 1-based last line of the match (inclusive). */
  end_line: number;
  /** scancode match score (0-100), or null when unreported. */
  score: number | null;
}

export interface SourceFileResponse {
  /** The scan whose preserved source this file was read from. */
  scan_id: string;
  /** POSIX path of the file relative to the source root. */
  path: string;
  /** Full uncompressed size of the file in bytes. */
  byte_size: number;
  /** True when `content` was capped at the viewer's per-file byte limit. */
  truncated: boolean;
  /** 'utf-8' when `content` is decoded text; 'binary' when non-text. */
  encoding: FileEncoding;
  /** Decoded file content (possibly truncated). Null for binary files. */
  content: string | null;
  /** Per-line license matches for this path. Empty when none recorded. */
  license_matches: LicenseMatch[];
}

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

export interface GetSourceTreeParams {
  /** Directory whose immediate children to list. Empty / undefined = root. */
  path?: string;
  /** 1-based page index (default 1 on the server). */
  page?: number;
  /** Page size (max 500 on the server). */
  size?: number;
  /** Scan to read; defaults to the project's latest scan. */
  scanId?: string;
}

export interface GetSourceFileParams {
  /** File to read, relative to the source root. Required. */
  path: string;
  /** Scan to read; defaults to the project's latest scan. */
  scanId?: string;
}

// ---------------------------------------------------------------------------
// Wire calls
// ---------------------------------------------------------------------------

/** List the immediate children of one directory in a scan's preserved source. */
export async function getSourceTree(
  projectId: string,
  params: GetSourceTreeParams = {},
): Promise<SourceTreePage> {
  const query: Record<string, unknown> = {};
  // Empty path is the root; the server defaults `path=""` so we only send a
  // non-empty value.
  if (params.path != null && params.path.length > 0) query.path = params.path;
  if (params.page != null) query.page = params.page;
  if (params.size != null) query.size = params.size;
  if (params.scanId != null && params.scanId.length > 0) {
    query.scan_id = params.scanId;
  }
  const { data } = await api.get<SourceTreePage>(
    `/v1/projects/${projectId}/source-tree`,
    { params: query },
  );
  return data;
}

/** Read one file (capped) + its per-line license matches. */
export async function getSourceFile(
  projectId: string,
  params: GetSourceFileParams,
): Promise<SourceFileResponse> {
  const query: Record<string, unknown> = { path: params.path };
  if (params.scanId != null && params.scanId.length > 0) {
    query.scan_id = params.scanId;
  }
  const { data } = await api.get<SourceFileResponse>(
    `/v1/projects/${projectId}/source-file`,
    { params: query },
  );
  return data;
}
