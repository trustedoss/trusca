/**
 * Localize a project-surface `ProblemError` into an i18n key (BUG-002).
 *
 * The backend returns RFC 7807 `application/problem+json` with English
 * `title`/`detail` fields. Rendering those verbatim leaks English copy into a
 * KO locale (e.g. "Project Not Found" / "project <id> not found"). This helper
 * mirrors the `features/admin/lib/adminErrorMessage.ts` pattern: it maps the
 * Problem to a stable, locale-independent i18n key the caller resolves with
 * `t()`, so the message follows the active language.
 *
 * Mapping is by HTTP status (the project read surface has no domain extension
 * flags today, unlike the admin surface):
 *   - 404 → `<prefix>.not_found`
 *   - 403 → `<prefix>.forbidden`
 *   - anything else / non-Problem → `<prefix>.unknown`
 *
 * Caller pattern:
 *
 *   const key = projectErrorMessageKey(err, "page.errors");
 *   return <span>{t(key)}</span>;
 */
import { ProblemError } from "@/lib/problem";

/** The token (sans prefix) for the matched error class. */
export type ProjectErrorToken = "not_found" | "forbidden" | "unknown";

/**
 * Classify a project-surface error into one of the known tokens. Non-Problem
 * errors (network / unexpected) fall back to `"unknown"`.
 */
export function projectErrorToken(err: unknown): ProjectErrorToken {
  if (!(err instanceof ProblemError)) return "unknown";
  if (err.status === 404) return "not_found";
  if (err.status === 403) return "forbidden";
  return "unknown";
}

/**
 * Return the fully-qualified i18n key for a project-surface error, scoped to a
 * caller-supplied namespace prefix (e.g. `"page.errors"` or
 * `"overview.gate_card.errors"`). The prefix MUST expose `not_found`,
 * `forbidden`, and `unknown` sub-keys.
 */
export function projectErrorMessageKey(err: unknown, prefix: string): string {
  return `${prefix}.${projectErrorToken(err)}`;
}
