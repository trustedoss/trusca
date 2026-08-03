// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * demoSandbox — feat/demo-sandbox-scan helpers.
 *
 * The public read-only demo can enable a narrow "sandbox" carve-out
 * (`/health.demo_sandbox_scans`, see {@link useDemoMode}) that permits exactly
 * two writes against a single seeded project: triggering a source scan and
 * ingesting a CycloneDX/SPDX SBOM. Every other write, and every other project,
 * stays read-only — the backend middleware is the real guard; these helpers
 * just let the UI re-open the matching affordances instead of dead-end clicks
 * that would 403.
 *
 * The seed creates the project with EXACTLY the name below (demo team). We match
 * by name because the seeded id is not known to the SPA build. This is a
 * demo-only convenience, so a name match is acceptable (documented as such in
 * the task brief); it is deliberately the only hard-coded string.
 */

/** The exact name the demo seed gives the sandbox project. */
export const DEMO_SANDBOX_PROJECT_NAME = "Demo Sandbox";

/**
 * The server-enforced size cap for a live sandbox source scan, surfaced in the
 * guidance copy so a visitor knows the threshold before uploading. Kept in sync
 * with the backend `DEMO_SANDBOX_SCAN_MAX_BYTES` by intent; the backend remains
 * the authoritative guard (an over-size upload fails there regardless).
 */
export const DEMO_SANDBOX_SCAN_MAX_MB = 10;

/**
 * Whether `name` is the seeded sandbox project. Trims and compares
 * case-sensitively against {@link DEMO_SANDBOX_PROJECT_NAME} — the seed writes
 * the canonical casing, so a strict match avoids accidentally re-enabling
 * writes on a user-named "demo sandbox" project in a non-demo deploy.
 */
export function isDemoSandboxProjectName(name: string | null | undefined): boolean {
  return typeof name === "string" && name.trim() === DEMO_SANDBOX_PROJECT_NAME;
}

/**
 * Resolve the BomLens project URL for the "scan larger projects locally" hint.
 * Read from the optional build-time `VITE_BOMLENS_URL` (so a deployer can point
 * at an internal mirror) with a public default. Read inside the function per
 * CLAUDE.md rule #11 (no module-scope env caching).
 */
export function bomLensUrl(): string {
  const raw = import.meta.env.VITE_BOMLENS_URL as string | undefined;
  const trimmed = typeof raw === "string" ? raw.trim() : "";
  return trimmed.length > 0 ? trimmed : "https://github.com/sktelecom/bomlens";
}
