/**
 * Map a `ProblemError` from the scan-cancel endpoint to a user-facing i18n
 * key + a stable, locale-independent extension token (PR-A3).
 *
 * Backend contract (`POST /v1/scans/{scan_id}/cancel`, PR-A1):
 *   - 409 + `scan_already_cancelled: true`  → scan reached a terminal state.
 *   - 404 + `scan_not_found: true`          → unknown id OR other-team scan
 *                                             (existence-hide).
 *   - 403                                   → caller lacks the developer role.
 *
 * Both extension flags are whitelisted in `lib/problem.ts`, so they survive
 * the Problem sanitizer. The token return is used on the toast markup so e2e
 * tests can assert on the invariant without depending on translated copy.
 *
 * Caller pattern:
 *
 *   try { await cancel.mutateAsync({ scanId }); }
 *   catch (err) {
 *     notify(t(scanCancelErrorKey(err)), "error", scanCancelErrorToken(err));
 *   }
 */
import { ProblemError } from "@/lib/problem";

const EXTENSION_KEY_MAP: Array<[string, string]> = [
  ["scan_already_cancelled", "cancel.errors.already_terminal"],
  ["scan_not_found", "cancel.errors.not_found"],
];

const EXTENSION_TOKENS: ReadonlyArray<string> = [
  "scan_already_cancelled",
  "scan_not_found",
];

function matchedExtension(err: unknown): string | null {
  if (!(err instanceof ProblemError) || !err.problem) return null;
  const extras = err.problem as unknown as Record<string, unknown>;
  for (const token of EXTENSION_TOKENS) {
    if (extras[token] === true) return token;
  }
  return null;
}

/**
 * Returns the i18n key (relative to the `scans` namespace) best matching the
 * failure. Callers hold `useTranslation("scans")` so they can pass this key
 * straight to `t()`.
 */
export function scanCancelErrorKey(err: unknown): string {
  const token = matchedExtension(err);
  if (token) {
    const entry = EXTENSION_KEY_MAP.find(([field]) => field === token);
    if (entry) return entry[1];
  }
  if (err instanceof ProblemError) {
    if (err.status === 409) return "cancel.errors.already_terminal";
    if (err.status === 404) return "cancel.errors.not_found";
    if (err.status === 403) return "cancel.errors.forbidden";
  }
  return "cancel.errors.unknown";
}

/**
 * The stable token surfaced on the toast markup (`data-toast-key`). Falls
 * back to the HTTP status family when no recognised extension is present.
 */
export function scanCancelErrorToken(err: unknown): string {
  const token = matchedExtension(err);
  if (token) return token;
  if (err instanceof ProblemError) {
    if (err.status === 409) return "scan_already_cancelled";
    if (err.status === 404) return "scan_not_found";
    if (err.status === 403) return "forbidden";
  }
  return "unknown";
}
