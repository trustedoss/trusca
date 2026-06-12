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
import { isDemoReadOnlyError } from "@/lib/demoReadOnly";
import { ProblemError } from "@/lib/problem";

/**
 * Fully-qualified i18n key (namespace-prefixed) for the read-only-demo case.
 * It lives in the shared `common` namespace rather than under each caller's
 * `prefix` so a write blocked by the demo guard surfaces one consistent
 * message without every prefix having to define its own `demo_read_only`
 * sub-key.
 */
export const DEMO_READ_ONLY_MESSAGE_KEY = "common:demo.write_disabled";

/** The token (sans prefix) for the matched error class. */
export type ProjectErrorToken =
  | "not_found"
  | "forbidden"
  | "demo_read_only"
  | "unknown";

/**
 * Classify a project-surface error into one of the known tokens. Non-Problem
 * errors (network / unexpected) fall back to `"unknown"`.
 *
 * The read-only-demo 403 is checked FIRST: the demo middleware runs before
 * auth and returns 403, so without this branch a demo write would be
 * mislabelled as a permission denial (`forbidden`).
 */
export function projectErrorToken(err: unknown): ProjectErrorToken {
  if (isDemoReadOnlyError(err)) return "demo_read_only";
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
 *
 * The read-only-demo case is the one exception: it resolves to the shared
 * `common:demo.write_disabled` key (already namespace-qualified) instead of
 * `<prefix>.demo_read_only`, so callers do not need a per-prefix sub-key.
 */
export function projectErrorMessageKey(err: unknown, prefix: string): string {
  const token = projectErrorToken(err);
  if (token === "demo_read_only") return DEMO_READ_ONLY_MESSAGE_KEY;
  return `${prefix}.${token}`;
}
