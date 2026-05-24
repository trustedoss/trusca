/**
 * Translate a `ProblemError` from the license-policy API into a stable,
 * locale-independent extension token + an i18n key.
 *
 * The c1 backend (`apps/backend/api/v1/license_policies.py`) surfaces failures
 * as RFC 7807 envelopes keyed mainly by HTTP status — it does NOT emit the
 * boolean extension flags the admin surface uses:
 *
 *   - 403 → caller is not a team_admin of the team (read-only fallback).
 *   - 404 → team / org policy not found, or existence-hide for a non-admin.
 *   - 409 → uniqueness race on the (org, team) scope.
 *   - 422 → malformed / oversized policy payload (the strict pydantic guards).
 *
 * Caller pattern:
 *   try { await mutation.mutateAsync(...) }
 *   catch (err) { notify(t(policyErrorMessageKey(err)), "error", policyErrorToken(err)); }
 */
import { ProblemError } from "@/lib/problem";

/** The stable token surfaced as ``data-toast-key`` for e2e assertions. */
export function policyErrorToken(err: unknown): string {
  if (!(err instanceof ProblemError)) {
    return "unknown";
  }
  switch (err.status) {
    case 403:
      return "forbidden";
    case 404:
      return "not_found";
    case 409:
      return "conflict";
    case 422:
      return "validation";
    default:
      return "unknown";
  }
}

/** Returns the `policies.errors.*` i18n key best matching the problem. */
export function policyErrorMessageKey(err: unknown): string {
  return `policies.errors.${policyErrorToken(err)}`;
}
