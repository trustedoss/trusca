// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Turn a `ProblemError` into text the user can read in their own language.
 *
 * The backend answers with RFC 7807, and its `detail` is always populated and
 * always English. Preferring it over a translation key was the obvious thing
 * to write at every call site, and it is why a Korean session met English on
 * every failed request while the rest of the product was translated. The keys
 * existed; the backend just outranked them.
 *
 * The rule here inverts that: classify the problem, translate the class, and
 * fall back to `detail` only for a class we have no wording for. The fallback
 * stays because a specific English sentence beats a generic Korean one when
 * the alternative is "the request failed" — but it is the exception now, and
 * `scripts/problem-detail-lint.mjs` keeps count of the call sites that still
 * reach for `detail` directly.
 *
 * Domain surfaces that map their own extension fields (see
 * `features/admin/lib/adminErrorMessage.ts` and
 * `features/projects/lib/projectErrorMessage.ts`) stay as they are. They
 * classify further than this can — `last_super_admin_protected` is not an
 * HTTP status — and both already resolve to keys rather than `detail`. This
 * helper is the floor for everything else, not a replacement for them.
 */
import type { TFunction } from "i18next";

import { isDemoReadOnlyError } from "@/lib/demoReadOnly";
import { ProblemError } from "@/lib/problem";

/**
 * The error classes shared by every surface. Anything narrower belongs in a
 * domain mapper, not here.
 */
export type ProblemToken =
  | "demo_read_only"
  | "network"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "rate_limited"
  | "server_error"
  | "unknown";

/** Fully-qualified key for the read-only demo, shared across surfaces. */
export const DEMO_READ_ONLY_KEY = "common:demo.write_disabled";

/** The `common:errors.*` key each token resolves to. */
const COMMON_KEY_BY_TOKEN: Record<ProblemToken, string> = {
  demo_read_only: DEMO_READ_ONLY_KEY,
  network: "common:errors.network",
  unauthorized: "common:errors.unauthorized",
  forbidden: "common:errors.forbidden",
  not_found: "common:errors.not_found",
  conflict: "common:errors.conflict",
  rate_limited: "common:errors.rate_limited",
  server_error: "common:errors.server_error",
  unknown: "common:errors.request_failed",
};

/**
 * Classify an error. Order matters at the top: the read-only demo guard runs
 * before auth and answers 403, so checking it first keeps a blocked demo write
 * from being reported as a permission denial.
 */
export function problemToken(err: unknown): ProblemToken {
  if (isDemoReadOnlyError(err)) return "demo_read_only";
  if (!(err instanceof ProblemError)) return "unknown";
  // `lib/api.ts` normalizes a transport failure (no response at all) to
  // status 0, which is the only way the UI can tell "server said no" from
  // "never reached the server".
  if (err.status === 0) return "network";
  if (err.status === 401) return "unauthorized";
  if (err.status === 403) return "forbidden";
  if (err.status === 404) return "not_found";
  if (err.status === 409 || err.status === 412) return "conflict";
  if (err.status === 429) return "rate_limited";
  if (err.status >= 500) return "server_error";
  return "unknown";
}

export interface ProblemMessageOptions {
  /**
   * Namespace-scoped prefix to try before the shared wording, e.g.
   * `"project_detail:overview.errors"`. A key under it wins when it exists,
   * which is how a surface says "this project no longer exists" instead of
   * the generic not-found sentence.
   */
  prefix?: string;
  /**
   * Set false to keep the backend's English `detail` out of the result
   * entirely, for surfaces that would rather show generic localized copy than
   * untranslated text.
   */
  allowDetailFallback?: boolean;
}

/**
 * Resolve an error to display text.
 *
 * Resolution order: a prefixed key for the token, the shared `common:errors`
 * wording for the token, the backend `detail`, then the generic sentence. An
 * `unknown` token skips straight to `detail`, because "the request failed" is
 * the message we have precisely when we know nothing.
 */
export function problemMessage(
  err: unknown,
  t: TFunction,
  options: ProblemMessageOptions = {},
): string {
  const { prefix, allowDetailFallback = true } = options;
  const token = problemToken(err);

  if (prefix) {
    const scoped = t(`${prefix}.${token}`, { defaultValue: "" });
    if (scoped) return scoped;
  }

  if (token !== "unknown") {
    const shared = t(COMMON_KEY_BY_TOKEN[token], { defaultValue: "" });
    if (shared) return shared;
  }

  if (
    allowDetailFallback &&
    err instanceof ProblemError &&
    err.detail &&
    // A transport failure's `detail` is the axios message ("Network Error"),
    // which is both English and meaningless to the user.
    token !== "network"
  ) {
    return err.detail;
  }

  return t("common:errors.request_failed");
}
