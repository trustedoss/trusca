// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Pre-adoption catalog lookup: a package on deps.dev, before anything has
 * scanned it.
 *
 * `internal_projects` already comes back on this same response -- the
 * backend cross-references the resolved purl against internal scan data
 * itself (`services.external_package_usage`), so there is no second call to
 * make here.
 */
import { api } from "@/lib/api";

export const EXTERNAL_PACKAGE_ECOSYSTEMS = [
  "npm",
  "pypi",
  "maven",
  "go",
  "cargo",
  "nuget",
] as const;

export type ExternalPackageEcosystem = (typeof EXTERNAL_PACKAGE_ECOSYSTEMS)[number];

export interface InternalProjectUsage {
  project_id: string;
  project_name: string;
  project_slug: string;
  /** The version this project's current scan actually carries -- may
   * differ from the externally-resolved `version` above it. */
  version: string;
}

export interface ExternalPackageLookupOut {
  ecosystem: string;
  name: string;
  found: boolean;
  version: string | null;
  /** Versionless -- matches `Component.purl`'s own identity. */
  purl: string | null;
  licenses: string[];
  advisory_count: number;
  advisory_ids: string[];
  homepage_url: string | null;
  source_repo_url: string | null;
  internal_projects: InternalProjectUsage[];
}

export async function lookupExternalPackage(
  ecosystem: string,
  name: string,
): Promise<ExternalPackageLookupOut> {
  const { data } = await api.get<ExternalPackageLookupOut>("/v1/external-packages", {
    params: { ecosystem, name },
  });
  return data;
}
