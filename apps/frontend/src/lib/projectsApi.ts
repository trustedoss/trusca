/**
 * Projects + scans REST surface — Phase 2 PR #9 task 2.11.
 *
 * Thin typed wrapper around the existing `api` axios instance. We keep these
 * functions free of TanStack Query so the same calls can be used in mutations,
 * tests, or imperative code paths.
 *
 * Backend contracts come from:
 *   - apps/backend/api/v1/projects.py
 *   - apps/backend/api/v1/scans.py
 *   - apps/backend/schemas/scan.py
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types — mirror the backend schemas/scan.py wire shapes (snake_case).
// ---------------------------------------------------------------------------

export type ProjectVisibility = "team" | "organization";
/**
 * Closed scan-kind set — runtime mirror of the backend's scan `kind` values
 * (`source` cdxgen→SBOM, `container` image scan, `sbom` external CycloneDX
 * ingest, PR #406). Same pattern as `SCAN_STATUS_VALUES`: the array is walked
 * by `tests/unit/contracts/catalogMirrors.test.ts` to assert every kind owns
 * its own `page.kind.*` / `overview.recent_scans.kind.*` /
 * `scans.filter.kind.*` label in both locales, so a kind added on the backend
 * fails a PR-time vitest instead of silently rendering a raw i18n key.
 */
export const SCAN_KIND_VALUES = ["source", "container", "sbom"] as const;

export type ScanKind = (typeof SCAN_KIND_VALUES)[number];
/**
 * Closed scan status set — runtime mirror of the backend's
 * `models/scan.py::SCAN_STATUS_VALUES`, same order. PR-6 FE regression
 * guards: `tests/unit/contracts/catalogMirrors.test.ts` walks this array to
 * assert every status owns its own `status.*` label in both locales and its
 * own badge visual, so a status added on the backend (or a copy/paste like
 * the cancelled-badge-says-Failed bug) fails a PR-time vitest.
 */
export const SCAN_STATUS_VALUES = [
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
] as const;

export type ScanStatus = (typeof SCAN_STATUS_VALUES)[number];

export interface ProjectPublic {
  id: string;
  team_id: string;
  name: string;
  slug: string;
  description: string | null;
  git_url: string | null;
  default_branch: string | null;
  visibility: ProjectVisibility;
  archived_at: string | null;
  created_by_user_id: string | null;
  latest_scan_id: string | null;
  /**
   * Status of the latest scan *attempt* for this project (most recent row,
   * regardless of outcome). `null` when the project has never been scanned.
   * Drives the per-row status badge on the project list.
   */
  latest_scan_status: ScanStatus | null;
  /**
   * Vuln-severity component counts from the latest *succeeded* scan. `null`
   * when the project has no succeeded scan yet (so the list row renders no
   * severity summary). Buckets may legitimately be 0.
   */
  severity_summary: ProjectSeveritySummary | null;
  /**
   * License-category component counts from the project's latest succeeded
   * scan (same anchor as ``severity_summary``). Buckets follow the
   * dashboard rank: forbidden > conditional > allowed > unknown. The
   * Projects-page License card collapses each project's counts to the
   * worst non-zero bucket; the segment-click filter narrows the list to
   * projects whose worst bucket matches. ``null`` when the project has
   * no succeeded scan. Populated only on the list endpoint.
   */
  license_category_summary: ProjectLicenseCategorySummary | null;
  /**
   * Display label for the project's creator — the user's ``full_name``
   * (when set) or email. ``null`` when the user row was deleted. Populated
   * only on the list endpoint; the FE list table renders this in a
   * Created-by column.
   */
  created_by_user_name: string | null;
  /**
   * Whether a git credential (PAT/token) is stored for cloning private https
   * repos (feature #18). The plaintext/ciphertext value is NEVER returned by
   * the backend — only this boolean presence flag.
   */
  has_git_credential: boolean;
  /**
   * Total scan attempts for this project (any status). Populated only on the
   * list endpoint (`GET /v1/projects`); single-project responses default to 0.
   * Always a number — never null — so a never-scanned project comes back as 0.
   * Drives the per-row "Scn N" discoverability badge (W3 #30).
   */
  scan_count: number;
  /**
   * Count of *succeeded* scans (release model — every succeeded scan IS a
   * release snapshot). Populated only on the list endpoint; single-project
   * responses default to 0. Always a number — never null.
   * Drives the per-row "Rel N" discoverability badge (W3 #30).
   */
  release_count: number;
  /**
   * ISO-8601 timestamp of the most recent scan *attempt* (any status). `null`
   * when the project has never been scanned. Populated only on the list
   * endpoint; single-project responses default to null.
   * Drives the per-row relative-time label (W3 #30).
   */
  last_scan_at: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * Per-project vulnerability-severity component counts (from the latest
 * succeeded scan). Mirrors the backend `severity_summary` wire object. Every
 * bucket is present; a missing succeeded scan is signalled by the whole object
 * being `null` on {@link ProjectPublic}, not by omitting buckets.
 */
export interface ProjectSeveritySummary {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

export interface ProjectLicenseCategorySummary {
  forbidden: number;
  conditional: number;
  allowed: number;
  unknown: number;
}

export interface ProjectListResponse {
  items: ProjectPublic[];
  total: number;
  page: number;
  size: number;
}

export interface ScanPublic {
  id: string;
  project_id: string;
  kind: ScanKind;
  status: ScanStatus;
  progress_percent: number;
  current_step: string | null;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  requested_by_user_id: string | null;
  celery_task_id: string | null;
  metadata: Record<string, unknown>;
  /**
   * Optional release/version label this scan was triggered against (feature
   * #18), e.g. `v1.2.3`. Passed through `metadata.release` on trigger and
   * surfaced here on read models. `null` when no release was supplied.
   */
  release: string | null;
  /**
   * P1 #5 — denormalised project name/slug surfaced on every scan row by the
   * list endpoint (`GET /v1/scans` and `GET /v1/projects/{id}/scans`) so the
   * cross-project Scans queue can render the project in a human label and
   * link to `/projects/{project_id}` instead of showing the first 8 chars of
   * the UUID. Declared optional (not required-nullable) so test fixtures and
   * single-row endpoints don't have to populate them — the UI guards with a
   * truthy check and falls back to `project_id` when absent.
   */
  project_name?: string | null;
  project_slug?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScanListResponse {
  items: ScanPublic[];
  total: number;
  page: number;
  size: number;
}

export interface ProjectCreatePayload {
  team_id: string;
  name: string;
  slug: string;
  description?: string | null;
  git_url?: string | null;
  default_branch?: string | null;
  visibility?: ProjectVisibility;
}

export interface ProjectUpdatePayload {
  name?: string;
  description?: string | null;
  git_url?: string | null;
  default_branch?: string | null;
  visibility?: ProjectVisibility;
  /**
   * Write-only git credential (feature #18). Set a non-empty token to store it
   * encrypted for cloning private https repos. Never returned on read — the
   * presence is reflected by `ProjectPublic.has_git_credential`. Do NOT send
   * together with `clear_git_credential: true` (backend → 422).
   */
  git_credential?: string;
  /** Clear the stored git credential. Mutually exclusive with `git_credential`. */
  clear_git_credential?: boolean;
}

export interface ScanTriggerPayload {
  kind?: ScanKind;
  metadata?: Record<string, unknown>;
}

export interface ListProjectsParams {
  team_id?: string;
  include_archived?: boolean;
  q?: string;
  page?: number;
  size?: number;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export async function listProjects(
  params: ListProjectsParams = {},
  config?: AxiosRequestConfig,
): Promise<ProjectListResponse> {
  const { data } = await api.get<ProjectListResponse>("/v1/projects", {
    ...config,
    params: {
      team_id: params.team_id,
      include_archived: params.include_archived,
      q: params.q,
      page: params.page,
      size: params.size,
    },
  });
  return data;
}

export async function createProject(
  payload: ProjectCreatePayload,
): Promise<ProjectPublic> {
  const { data } = await api.post<ProjectPublic>("/v1/projects", payload);
  return data;
}

export async function getProject(projectId: string): Promise<ProjectPublic> {
  const { data } = await api.get<ProjectPublic>(`/v1/projects/${projectId}`);
  return data;
}

export async function updateProject(
  projectId: string,
  payload: ProjectUpdatePayload,
): Promise<ProjectPublic> {
  const { data } = await api.patch<ProjectPublic>(
    `/v1/projects/${projectId}`,
    payload,
  );
  return data;
}

export async function archiveProject(projectId: string): Promise<void> {
  await api.delete(`/v1/projects/${projectId}`);
}

/**
 * Unarchive a previously archived project. Backed by `PATCH /v1/projects/{id}`
 * with `archived: false`; if the backend rejects the field the call surfaces
 * as a ProblemError to the caller.
 */
export async function unarchiveProject(
  projectId: string,
): Promise<ProjectPublic> {
  const { data } = await api.patch<ProjectPublic>(
    `/v1/projects/${projectId}`,
    { archived: false },
  );
  return data;
}

/**
 * Trigger a scan for a project. The backend returns `202 Accepted` with the
 * Scan row already persisted; the WebSocket then streams progress.
 */
export async function triggerScan(
  projectId: string,
  payload: ScanTriggerPayload = {},
): Promise<ScanPublic> {
  const { data } = await api.post<ScanPublic>(
    `/v1/projects/${projectId}/scans`,
    {
      kind: payload.kind ?? "source",
      metadata: payload.metadata ?? {},
    },
  );
  return data;
}

export async function getScan(scanId: string): Promise<ScanPublic> {
  const { data } = await api.get<ScanPublic>(`/v1/scans/${scanId}`);
  return data;
}

// ---------------------------------------------------------------------------
// Received-SBOM conformance — model 3 (external SBOM ingest).
//
// `GET /v1/projects/{project_id}/scans/{scan_id}/conformance` returns a
// quality verdict for an uploaded SBOM (`kind: "sbom"` scans only). The verdict
// scores how usable the document is for SCA matching: PURL/license/hash
// coverage plus a list of named checks. A 404 means either the project is
// unreachable OR no verdict exists yet (existence-hide); the UI treats both as
// "no panel" rather than an error.
// ---------------------------------------------------------------------------

export type SbomSourceFormat =
  | "cyclonedx"
  | "spdx-json"
  | "spdx-tv"
  | "unknown";

export type SbomConformanceResult = "pass" | "warn" | "fail";

export type SbomCheckStatus = "pass" | "fail" | "warn";

export interface SbomConformanceCheck {
  id: string;
  label: string;
  required: boolean;
  status: SbomCheckStatus;
  detail: string;
  missing: string[];
  // --- G7 AI SBOM extension (feat/g7-conformance) ---------------------------
  // Optional fields carried only by the advisory G7 minimum-element checks
  // (ids prefixed "g7-", always required=false, status pass|warn). The 9 core
  // format checks leave them null/absent, so their render path is unchanged.
  /** G7 cluster id: metadata|slp|models|dp|infrastructure|sp|kpi. */
  cluster?: string | null;
  /**
   * Where a satisfied value comes from: auto (read directly), inferred
   * (derived from signals), declared (only if a human/manifest supplied it),
   * na (no automated source — requires human review).
   */
  source?: string | null;
  /** The party the G7 text names as the provider (informational, not a gate). */
  role?: string | null;
  /** Actual SBOM values that satisfied the element (purl, license id, …). */
  evidence?: string[] | null;
}

export interface SbomConformanceRead {
  scan_id: string;
  project_id: string;
  source_format: SbomSourceFormat;
  result: SbomConformanceResult;
  n_fail: number;
  n_warn: number;
  component_count: number;
  purl_coverage_pct: number | null;
  license_coverage_pct: number | null;
  hash_coverage_pct: number | null;
  checks: SbomConformanceCheck[];
}

/**
 * Canonical conformance check ids, in evaluation order.
 *
 * Runtime mirror of the backend's
 * `services/sbom_conformance.CHECK_IDS` — kept in lock-step by the FE↔BE
 * catalog-mirror contract test (`tests/unit/contracts/catalogMirrors.test.ts`)
 * so a check added on the backend fails a PR-time vitest instead of silently
 * rendering a raw `conformance.check_id.*` i18n key (or no label at all).
 */
export const SBOM_CHECK_IDS = [
  "timestamp",
  "tools",
  "top-component",
  "name-version",
  "purl",
  "no-generic",
  "transitive",
  "license",
  "hash",
] as const;

export type SbomCheckId = (typeof SBOM_CHECK_IDS)[number];

export async function getSbomConformance(
  projectId: string,
  scanId: string,
): Promise<SbomConformanceRead> {
  const { data } = await api.get<SbomConformanceRead>(
    `/v1/projects/${projectId}/scans/${scanId}/conformance`,
  );
  return data;
}

/**
 * Cancel a queued/running scan owned by the current user's team (PR-A1).
 *
 * Backend contract — `POST /v1/scans/{scan_id}/cancel`:
 *   - `developer` role, own-team scans only (other teams 404 — existence-hide).
 *   - Already-terminal scans (succeeded/failed/cancelled) → 409 with the
 *     `scan_already_cancelled` Problem extension.
 *   - Unknown / cross-team scan id → 404 with `scan_not_found`.
 * On success the cancelled `ScanPublic` row is returned (status `cancelled`).
 * The WebSocket then streams a terminal `cancelled` frame on its own; this
 * call only flips the persisted row.
 */
export async function cancelScan(scanId: string): Promise<ScanPublic> {
  const { data } = await api.post<ScanPublic>(`/v1/scans/${scanId}/cancel`);
  return data;
}

export async function listScans(
  projectId: string,
  params: { page?: number; size?: number } = {},
): Promise<ScanListResponse> {
  const { data } = await api.get<ScanListResponse>(
    `/v1/projects/${projectId}/scans`,
    {
      params: { page: params.page, size: params.size },
    },
  );
  return data;
}

export interface ListMyScansParams {
  status?: ScanStatus;
  page?: number;
  size?: number;
}

/**
 * Cross-project scan queue for the current user. Backed by
 * `GET /v1/scans` with optional `status` filter and standard pagination.
 * Used by the global ScansPage in the sidebar — mirrors AdminScansPage but
 * is scoped to the user's reachable teams.
 */
export async function listMyScans(
  params: ListMyScansParams = {},
): Promise<ScanListResponse> {
  const { data } = await api.get<ScanListResponse>("/v1/scans", {
    params: {
      status: params.status,
      page: params.page,
      size: params.size,
    },
  });
  return data;
}

// ---------------------------------------------------------------------------
// SBOM export — Phase 3 / Step 4-A.
// ---------------------------------------------------------------------------

export type SbomFormat =
  | "cyclonedx-json"
  | "cyclonedx-xml"
  | "spdx-json"
  | "spdx-tv";

export interface SbomDownload {
  blob: Blob;
  filename: string;
  format: SbomFormat;
}

const SBOM_FALLBACK_EXTENSIONS: Record<SbomFormat, string> = {
  "cyclonedx-json": "cdx.json",
  "cyclonedx-xml": "cdx.xml",
  "spdx-json": "spdx.json",
  "spdx-tv": "spdx",
};

/**
 * Fetch the SBOM document as a Blob and parse the suggested filename out of
 * the `Content-Disposition` header. The caller wires the blob into a
 * `URL.createObjectURL` + `<a download>` click trigger; doing the request
 * through axios (rather than `window.location =`) keeps the bearer token
 * out of the URL / browser history / any reverse-proxy access logs.
 */
export async function downloadSbom(
  projectId: string,
  format: SbomFormat,
  options: { scanId?: string } = {},
): Promise<SbomDownload> {
  const params: Record<string, unknown> = { format };
  if (options.scanId != null && options.scanId.length > 0) {
    params.scan_id = options.scanId;
  }
  const response = await api.get<Blob>(`/v1/projects/${projectId}/sbom`, {
    params,
    responseType: "blob",
  });
  const headers = (response.headers ?? {}) as Record<string, string>;
  const disposition =
    headers["content-disposition"] ?? headers["Content-Disposition"] ?? "";
  // RFC 6266 filename — handles `filename="x.json"` and bare `filename=x.json`.
  const match = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/i);
  const filename =
    match?.[1] ?? `sbom-${projectId}.${SBOM_FALLBACK_EXTENSIONS[format]}`;
  return { blob: response.data as Blob, filename, format };
}

// ---------------------------------------------------------------------------
// SBOM signature & verification download — v2.3-s3.
//
// The backend (apps/backend/api/v1/sbom.py) exposes a small family of
// authenticated, IDOR-guarded (404 existence-hide) download endpoints that all
// reply with `Content-Disposition: attachment`. They let a consumer verify the
// project's signed SBOM offline with cosign:
//   - `signature-bundle` → a self-contained zip (SBOM + .sig + cert|public-key +
//     attestation + keyless attest cert + VERIFY.md). This is the recommended
//     single-button download.
//   - the individual artifacts below, for power users assembling their own flow.
//
// Key-based deployments emit NO Fulcio certificates: `certificate` and
// `attestation-certificate` return 404 there. That 404 is an expected branch,
// not an error — the caller renders it as "not applicable / keyless only".
// A scan that was never signed returns 404 from every endpoint.
// ---------------------------------------------------------------------------

/**
 * The signature artifacts the project detail SBOM tab can download. `bundle` is
 * the recommended zip; the rest are individual artifacts.
 */
export type SbomSignatureArtifact =
  | "bundle"
  | "signature"
  | "certificate"
  | "attestation"
  | "attestation-certificate"
  | "public-key";

export interface SbomSignatureDownload {
  blob: Blob;
  filename: string;
  artifact: SbomSignatureArtifact;
}

/** Backend route segment for each artifact, relative to `.../sbom`. */
const SBOM_SIGNATURE_PATHS: Record<SbomSignatureArtifact, string> = {
  bundle: "signature-bundle",
  signature: "signature",
  certificate: "certificate",
  attestation: "attestation",
  "attestation-certificate": "attestation-certificate",
  "public-key": "public-key",
};

/** Filename used if the server omits a `Content-Disposition` filename. */
const SBOM_SIGNATURE_FALLBACK_FILENAMES: Record<SbomSignatureArtifact, string> =
  {
    bundle: "sbom-signature-bundle.zip",
    signature: "sbom.sig",
    certificate: "sbom-certificate.pem",
    attestation: "sbom-attestation.json",
    "attestation-certificate": "sbom-attestation-certificate.pem",
    "public-key": "cosign.pub",
  };

/**
 * Fetch a signing artifact (or the verification bundle) as a Blob through the
 * authenticated axios instance so the bearer token rides the Authorization
 * header, never the URL/history. The caller wires the blob into a transient
 * `<a download>` click.
 *
 * Errors propagate as `ProblemError`; a 404 from the cert endpoints on a
 * key-based deployment is an expected "not applicable" branch the UI handles.
 */
export async function downloadSbomSignatureArtifact(
  projectId: string,
  artifact: SbomSignatureArtifact,
): Promise<SbomSignatureDownload> {
  const segment = SBOM_SIGNATURE_PATHS[artifact];
  const response = await api.get<Blob>(
    `/v1/projects/${projectId}/sbom/${segment}`,
    { responseType: "blob" },
  );
  const headers = (response.headers ?? {}) as Record<string, string>;
  const disposition =
    headers["content-disposition"] ?? headers["Content-Disposition"] ?? "";
  // RFC 6266 filename — handles `filename="x.zip"` and bare `filename=x.zip`.
  const match = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)"?/i);
  const filename =
    match?.[1] ?? SBOM_SIGNATURE_FALLBACK_FILENAMES[artifact];
  return { blob: response.data as Blob, filename, artifact };
}
