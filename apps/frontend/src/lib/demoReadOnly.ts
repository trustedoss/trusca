/**
 * Read-only demo error classifier — v2.1 Track B (B5).
 *
 * The public live demo runs the normal image with the backend `DEMO_READ_ONLY`
 * flag flipped on. A demo middleware (apps/backend/core/middleware.py) runs
 * BEFORE auth and rejects every write (anything outside the
 * `/auth/login|refresh|logout` allow-list) with:
 *
 *   HTTP 403  application/problem+json
 *   { type: "urn:trustedoss:problem:demo-read-only",
 *     title: "Read-only demo",
 *     status: 403,
 *     detail: "...",
 *     demo_read_only: true }
 *
 * Because it runs before auth, an unauthenticated write is ALSO a demo 403 (not
 * a 401). So a plain "403 forbidden" mapping would mislabel these as permission
 * denials. This helper is the single source of truth that the project + admin
 * error mappers branch on FIRST, so every write surface shows the same friendly
 * "this is a read-only demo, writes are disabled" message instead of a generic
 * forbidden error.
 *
 * Detection is intentionally tolerant: we match on EITHER the stable problem
 * `type` URN OR the `demo_read_only: true` extension flag (both are emitted by
 * the same middleware). Either alone is sufficient and unambiguous — no other
 * surface uses them.
 */
import { ProblemError } from "@/lib/problem";

/** Stable problem `type` URN emitted by the demo read-only middleware. */
export const DEMO_READ_ONLY_PROBLEM_TYPE =
  "urn:trustedoss:problem:demo-read-only";

/**
 * True when `err` is the read-only-demo 403 from the backend demo middleware.
 * Distinguishes it from an ordinary permission-denied 403.
 */
export function isDemoReadOnlyError(err: unknown): boolean {
  if (!(err instanceof ProblemError)) return false;
  const problem = err.problem;
  if (!problem) return false;
  if (problem.type === DEMO_READ_ONLY_PROBLEM_TYPE) return true;
  // The `demo_read_only` extension is whitelisted + zod-validated in
  // lib/problem.ts, so reading it here is the typed boolean (or absent).
  return (problem as Record<string, unknown>).demo_read_only === true;
}
