/**
 * Translate a `ProblemError` from the admin API surface into a user-facing
 * i18n key. The backend surfaces domain invariants via snake_case extension
 * fields (see `apps/backend/services/admin_*.py`); we map each to the matching
 * `admin.errors.*` key.
 *
 * Caller pattern:
 *
 *   try { await mutation.mutateAsync(...) }
 *   catch (err) {
 *     toast(t(adminErrorMessageKey(err)));
 *   }
 *
 * Unknown errors fall back to `admin.errors.unknown`.
 */
import { isDemoReadOnlyError } from "@/lib/demoReadOnly";
import { ProblemError } from "@/lib/problem";

const EXTENSION_KEY_MAP: Array<[string, string]> = [
  ["last_super_admin_protected", "admin.errors.last_super_admin_protected"],
  ["cannot_modify_self", "admin.errors.cannot_modify_self"],
  ["last_team_admin_protected", "admin.errors.last_team_admin_protected"],
  ["team_has_active_scans", "admin.errors.team_has_active_scans"],
  // Phase 4 PR #14 — operational error extensions.
  ["scan_already_cancelled", "admin.errors.scan_already_cancelled"],
  ["scan_not_found", "admin.errors.scan_not_found"],
  ["audit_export_too_large", "admin.errors.audit_export_too_large"],
];

/**
 * Returns the i18n key best matching the problem details payload. Status 409
 * is a slug conflict (the only 409 in the admin surface). Validation
 * extensions are inspected first because they're more specific than the
 * generic 422/409 fallbacks.
 */
export function adminErrorMessageKey(err: unknown): string {
  // The read-only-demo 403 runs before auth and must win over the generic
  // 403/forbidden mapping, so a demo write attempt reads as "demo is
  // read-only" rather than "you lack permission".
  if (isDemoReadOnlyError(err)) {
    return "admin.errors.demo_read_only";
  }
  if (!(err instanceof ProblemError)) {
    return "admin.errors.unknown";
  }
  const problem = err.problem;
  if (problem) {
    // The backend appends extension fields directly onto the problem JSON;
    // axios deserializes them as siblings of `type`/`title`/`status`. The
    // `ProblemDetails` shape doesn't carry an index signature, so we cast
    // here intentionally — cast-as-record is the cheapest correct option
    // (vs. a wider Problem subtype that would touch every error site).
    const extras = problem as unknown as Record<string, unknown>;
    for (const [field, key] of EXTENSION_KEY_MAP) {
      if (extras[field] === true) return key;
    }
  }
  if (err.status === 409) return "admin.errors.slug_conflict";
  return "admin.errors.unknown";
}

/**
 * Identify the snake_case extension token surfaced by the backend Problem
 * payload — used by the toast/alert markup so e2e tests can assert on the
 * specific invariant without depending on translated copy.
 *
 * Returns ``"slug_conflict"`` for the only 409 case, ``"unknown"`` when no
 * known extension is present.
 */
export function adminErrorExtension(err: unknown): string {
  // Mirror adminErrorMessageKey: the demo guard is the most specific match
  // and is surfaced as its own token for e2e assertions.
  if (isDemoReadOnlyError(err)) {
    return "demo_read_only";
  }
  if (!(err instanceof ProblemError)) {
    return "unknown";
  }
  const problem = err.problem;
  if (problem) {
    const extras = problem as unknown as Record<string, unknown>;
    for (const [field] of EXTENSION_KEY_MAP) {
      if (extras[field] === true) return field;
    }
  }
  if (err.status === 409) return "slug_conflict";
  return "unknown";
}
