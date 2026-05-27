/**
 * RFC 7807 Problem Details — shared error type used by every HTTP layer.
 *
 * Originally lived in `lib/authApi.ts` (PR #6 / 1.6, fetch-based). Promoted
 * out so the axios surface in `lib/api.ts` (PR #6 / 1.7) can throw the same
 * shape and the auth pages don't have to learn a second error class.
 *
 * Contract:
 *   - `status === 0` is reserved for transport-level failures (network, CORS,
 *     DNS, aborted). The backend never returns 0.
 *   - `detail` is always populated (falls back to `title`) so the UI can
 *     render `err.detail` without further branching.
 *   - `problem` is the parsed JSON when the server returned `application/
 *     problem+json`; `null` when the body wasn't JSON or the request never
 *     reached the server.
 *
 * Extension hardening (security-reviewer F10):
 *   RFC 7807 §3.2 allows arbitrary "extension members" alongside the standard
 *   fields. The previous parser passed them through with an unbounded index
 *   signature — a future backend change that accidentally puts a sensitive
 *   shape into a Problem extension would silently round-trip through this
 *   layer. We now run extensions through a zod schema that:
 *     - whitelists the known domain extension keys (last_super_admin_protected,
 *       cannot_modify_self, team_has_active_scans, last_team_admin_protected,
 *       team_id from F9, etc.) with explicit types;
 *     - accepts unknown keys ONLY as JSON primitives (string / number /
 *       boolean / null) — nested objects and arrays from unknown extension
 *       keys are dropped with a console warning so a leak of a sensitive
 *       shape (e.g. a stack trace nested under an unfamiliar key) is
 *       prevented automatically.
 */

import { z } from "zod";

/**
 * Domain Problem Details extension keys we know about today. Adding a new
 * extension key in the backend requires updating BOTH this whitelist and
 * the corresponding zod schema below — by design, so a future contributor
 * cannot accidentally surface a sensitive shape on the wire.
 *
 * snake_case to mirror the backend (RFC 7807 §3.2 says extension members
 * are arbitrary; we pin the convention).
 */
export const KNOWN_PROBLEM_EXTENSION_KEYS = [
  "last_super_admin_protected",
  "cannot_modify_self",
  // L1: "invalid_role_assignment" and "validation_error" were listed here but
  // the backend never emits them as extension boolean flags — removed.
  "team_has_active_scans",
  "last_team_admin_protected",
  "team_id", // F9 — team-not-found Problem Details
  // RFC 7807 sometimes sees `errors` as a sub-array on validation problems.
  // We keep it because our 422 envelope embeds Pydantic's redacted error
  // list. Strict-typed below.
  "errors",
  // Phase 4 PR #14 — admin operational endpoints (Scans / Disk / Audit /
  // Health). Each is a snake_case boolean flag that surfaces a specific
  // domain invariant. Adding them to the strict whitelist gives the UI a
  // stable, locale-independent error key without round-tripping through the
  // unknown-primitive fallback.
  "scan_already_cancelled",
  "scan_not_found",
  "audit_export_too_large",
] as const;

export type KnownProblemExtensionKey =
  (typeof KNOWN_PROBLEM_EXTENSION_KEYS)[number];

/** Standard RFC 7807 reserved fields — never treated as extensions. */
const RESERVED_PROBLEM_KEYS: ReadonlySet<string> = new Set([
  "type",
  "title",
  "status",
  "detail",
  "instance",
]);

/**
 * Strict schemas for each known extension key. ``z.unknown()`` is only used
 * for the validation-error sub-array because its shape is large + driven by
 * Pydantic; the standard primitive whitelist below is what gates new keys.
 */
const KNOWN_EXTENSION_SCHEMAS: Record<KnownProblemExtensionKey, z.ZodTypeAny> = {
  last_super_admin_protected: z.boolean(),
  cannot_modify_self: z.boolean(),
  team_has_active_scans: z.boolean(),
  last_team_admin_protected: z.boolean(),
  team_id: z.string(),
  errors: z.array(z.unknown()).optional(),
  scan_already_cancelled: z.boolean(),
  scan_not_found: z.boolean(),
  audit_export_too_large: z.boolean(),
};

/**
 * The shape we accept for an extension value when the key is NOT in the
 * whitelist. Primitive-only — nested objects / arrays from unknown keys are
 * dropped, on the theory that a backend change that accidentally leaks a
 * complex shape (stack trace fragment, internal config, etc.) under a
 * brand-new key should not silently land in the UI's error envelope.
 */
const UNKNOWN_EXTENSION_PRIMITIVE = z.union([
  z.string(),
  z.number(),
  z.boolean(),
  z.null(),
]);

export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
  /**
   * RFC 7807 §3.2 allows arbitrary "extension members" alongside the standard
   * fields. The backend uses snake_case extension fields to surface domain
   * invariants — e.g. ``last_super_admin_protected: true``,
   * ``cannot_modify_self: true``, ``team_has_active_scans: true``. We carry
   * them through verbatim (after schema validation, see ``parseProblemBody``)
   * via an index signature so callers don't have to cast the whole problem
   * to ``Record<string, unknown>`` to read one field.
   */
  [extension: string]: unknown;
}

export class ProblemError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string;
  readonly problem: ProblemDetails | null;

  constructor(
    message: string,
    options: {
      status: number;
      title: string;
      detail: string;
      problem: ProblemDetails | null;
    },
  ) {
    super(message);
    this.name = "ProblemError";
    this.status = options.status;
    this.title = options.title;
    this.detail = options.detail;
    this.problem = options.problem;
  }
}

/**
 * Filter a raw extension map through the known-key whitelist + the
 * primitive-only fallback for unknown keys. Returns the sanitized map.
 *
 * Side effect: logs a console.warn for each rejection so a backend change
 * that lands a new extension key shows up in the dev console but cannot
 * silently round-trip through to the UI.
 */
function sanitizeExtensions(
  raw: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (KNOWN_PROBLEM_EXTENSION_KEYS.includes(key as KnownProblemExtensionKey)) {
      const schema = KNOWN_EXTENSION_SCHEMAS[key as KnownProblemExtensionKey];
      const parsed = schema.safeParse(value);
      if (parsed.success) {
        out[key] = parsed.data;
      } else {
        // Known key with wrong type — drop. The backend contract was
        // violated; keeping a malformed value would be worse than the
        // graceful fallback (UI uses title/detail).
        console.warn(
          `[problem] dropping malformed extension ${key}:`,
          parsed.error.issues,
        );
      }
    } else {
      // Unknown key — accept ONLY primitives. Drops nested objects /
      // arrays (which could leak a sensitive shape from the backend).
      const parsed = UNKNOWN_EXTENSION_PRIMITIVE.safeParse(value);
      if (parsed.success) {
        out[key] = parsed.data;
      } else {
        console.warn(
          `[problem] dropping unknown non-primitive extension ${key}`,
        );
      }
    }
  }
  return out;
}

/**
 * Parse an arbitrary JSON-ish body into a {@link ProblemDetails} when the
 * shape matches RFC 7807. Returns null if the body isn't an object.
 *
 * Used by both the fetch-based legacy path (`lib/authApi.ts`) and the axios
 * response interceptor (`lib/api.ts`).
 */
export function parseProblemBody(
  data: unknown,
  fallback: { status: number; statusText?: string },
): { problem: ProblemDetails | null; title: string; detail: string } {
  let title = fallback.statusText || `HTTP ${fallback.status}`;
  let detail = "";
  let problem: ProblemDetails | null = null;
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const obj = data as Record<string, unknown>;
    if (typeof obj.title === "string") title = obj.title;
    if (typeof obj.detail === "string") detail = obj.detail;

    // Strip the standard fields, then sanitize the rest as extensions.
    const rawExtensions: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj)) {
      if (!RESERVED_PROBLEM_KEYS.has(key)) rawExtensions[key] = value;
    }
    const extensions = sanitizeExtensions(rawExtensions);

    problem = {
      ...extensions,
      type: typeof obj.type === "string" ? obj.type : "about:blank",
      title,
      status: typeof obj.status === "number" ? obj.status : fallback.status,
      detail: detail || title,
      instance: typeof obj.instance === "string" ? obj.instance : undefined,
    };
  }
  return { problem, title, detail: detail || title };
}
