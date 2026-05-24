/**
 * Remediation REST surface — v2.2 Track B (b3 frontend, UI for the b2 dry-run +
 * b3 auto-PR API).
 *
 * Thin typed wrapper around the shared `api` axios instance, free of TanStack
 * Query so it can be used in mutations, tests, and imperative code paths
 * (mirrors `lib/licensePoliciesApi.ts`).
 *
 * Backend contracts come from:
 *   - apps/backend/api/v1/remediation.py
 *   - apps/backend/schemas/remediation.py        (dry-run)
 *   - apps/backend/schemas/remediation_pr.py     (pull-request)
 *
 * All wire fields stay snake_case so the OpenAPI contract is the single source
 * of truth. Every 4xx/5xx is `application/problem+json` and surfaces as a
 * {@link ProblemError} via the shared `api` interceptor — the UI maps the
 * 403 / 409 problems to inline guidance + the rest to a toast/alert.
 */
import type { AxiosRequestConfig } from "axios";

import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types — mirror backend schemas (snake_case).
// ---------------------------------------------------------------------------

/** Where the edited manifest came from. Mirrors `ManifestSource`. */
export type ManifestSource = "override" | "preserved_source" | "none";

/** One npm component the dry-run proposes to bump (advisory). */
export interface DryRunRecommendation {
  package: string;
  current_version: string;
  recommended_version: string;
}

/** One applied range edit in the proposed manifest. */
export interface DependencyChange {
  package: string;
  section: string;
  before: string;
  after: string;
  changed: boolean;
}

/** A non-fatal note about the dry-run (skip reason / lockfile guidance). */
export interface RemediationWarning {
  code: string;
  package: string | null;
  detail: string;
}

/** The computed npm remediation dry-run. */
export interface NpmDryRunResponse {
  project_id: string;
  scan_id: string | null;
  ecosystem: string;
  manifest_source: ManifestSource;
  manifest_found: boolean;
  changed: boolean;
  edited_manifest: string | null;
  recommendations: DryRunRecommendation[];
  changes: DependencyChange[];
  warnings: RemediationWarning[];
  notes: string[];
}

/** Lifecycle of a persisted remediation PR. Mirrors `RemediationPRStatus`. */
export type RemediationPRStatus =
  | "creating"
  | "open"
  | "failed"
  | "superseded";

/** One package bump recorded on the PR (audit / human review). */
export interface RemediationPackageChange {
  package: string;
  /** The version the scan saw (advisory; may be null). */
  from: string | null;
  /** The minimum-safe upgrade target the PR applies. */
  to: string;
}

/** The persisted remediation-PR record returned to the UI. */
export interface RemediationPullRequest {
  id: string;
  project_id: string;
  ecosystem: string;
  repository_full_name: string;
  head_branch: string;
  base_branch: string;
  pr_number: number | null;
  pr_url: string | null;
  status: RemediationPRStatus;
  package_changes: RemediationPackageChange[];
  created_at: string;
  updated_at: string;
}

export interface RemediationPullRequestListPage {
  items: RemediationPullRequest[];
  total: number;
}

export interface RemediationManifestBody {
  /**
   * Raw package.json text to edit. When omitted, the backend best-effort reads
   * the manifest from the latest preserved scan source. The target repository
   * is NEVER part of this body — it is derived from the project's opted-in
   * GitHub App installation.
   */
  manifest?: string | null;
}

export interface ListRemediationPullRequestsParams {
  page?: number;
  page_size?: number;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

/**
 * Preview the npm dependency-bump edit for a project (dry-run, no PR).
 *
 * Always `200` — a no-op (`changed: false`) and a no-manifest result
 * (`manifest_source: "none"`) are valid answers, not errors. Member-gated on
 * the backend; a 404 means "not visible".
 */
export async function npmDryRun(
  projectId: string,
  body: RemediationManifestBody = {},
  config?: AxiosRequestConfig,
): Promise<NpmDryRunResponse> {
  const { data } = await api.post<NpmDryRunResponse>(
    `/v1/projects/${projectId}/remediation/npm/dry-run`,
    { manifest: body.manifest ?? null },
    config,
  );
  return data;
}

/**
 * Open (or return the idempotent existing) automated npm remediation PR.
 *
 * team_admin + opt-in gated on the backend:
 *   - `403` → the caller is a member but not a team admin.
 *   - `409` → the project is not opted in (no linked GitHub App installation).
 *   - `204` → nothing to remediate (no manifest change) — returned as `null`.
 *
 * The shared axios interceptor turns 4xx/5xx into a {@link ProblemError}; a
 * `204` carries no body, so we surface it as `null` for the caller to render
 * an "already up to date" affordance.
 */
export async function createNpmPullRequest(
  projectId: string,
  body: RemediationManifestBody = {},
): Promise<RemediationPullRequest | null> {
  const res = await api.post<RemediationPullRequest | null>(
    `/v1/projects/${projectId}/remediation/npm/pull-request`,
    { manifest: body.manifest ?? null },
  );
  if (res.status === 204) return null;
  return res.data ?? null;
}

/** List the project's automated remediation-PR records (newest first). */
export async function listRemediationPullRequests(
  projectId: string,
  params: ListRemediationPullRequestsParams = {},
  config?: AxiosRequestConfig,
): Promise<RemediationPullRequestListPage> {
  const { data } = await api.get<RemediationPullRequestListPage>(
    `/v1/projects/${projectId}/remediation/pull-requests`,
    {
      ...config,
      params: {
        page: params.page,
        page_size: params.page_size,
      },
    },
  );
  return data;
}
