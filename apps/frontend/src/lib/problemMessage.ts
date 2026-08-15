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
 * the alternative is "the request failed", but it is the exception now, and
 * `scripts/problem-detail-lint.mjs` keeps count of the call sites that still
 * reach for `detail` directly.
 *
 * Domain surfaces that map their own extension fields (see
 * `features/admin/lib/adminErrorMessage.ts` and
 * `features/projects/lib/projectErrorMessage.ts`) stay as they are. They
 * classify further than this can (`last_super_admin_protected` is not an
 * HTTP status), and both already resolve to keys rather than `detail`. This
 * helper is the floor for everything else, not a replacement for them.
 */
import type { TFunction } from "i18next";

import {
  DEMO_READ_ONLY_MESSAGE_KEY,
  isDemoReadOnlyError,
} from "@/lib/demoReadOnly";
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
  | "validation"
  | "rate_limited"
  | "server_error"
  | "unknown";

/** The `common:errors.*` key each token resolves to. */
const COMMON_KEY_BY_TOKEN: Record<ProblemToken, string> = {
  demo_read_only: DEMO_READ_ONLY_MESSAGE_KEY,
  network: "common:errors.network",
  // No shared sentence: a validation failure is answered by the backend's
  // `detail`, which names the field or rule that was rejected. See below.
  validation: "",
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
  // 409 covers far more than a concurrent edit here: a duplicate email, a
  // scan already queued, a policy name taken, a delete blocked by an active
  // scan. The shared wording says the request disagrees with current state
  // and stops there, because "someone else changed this first, reload and
  // retry" would be wrong about the cause and wrong about the remedy for
  // most of them. Surfaces that know which 409 they can get should name it
  // through `prefix`.
  // 400 and 422 both mean "the request itself was wrong", and both answer with
  // a detail naming what was wrong, an unsupported format, a field that
  // failed a rule. Only the server knows which.
  if (err.status === 400 || err.status === 422) return "validation";
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
   * Key for a sentence naming what failed, e.g. "Could not create the API
   * key." Most call sites have one already, as the fallback they passed to
   * `t()`. It is prepended to the class sentence, so the user gets both what
   * broke and why: "Could not create the API key. You do not have permission
   * to do this."
   *
   * It also stands alone for a class we cannot name, where it beats the
   * backend's English `detail`.
   */
  action?: string;
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
 * For a class we can name: the surface's own wording if it has some, else the
 * shared sentence, with the `action` sentence in front when given.
 *
 * For a class we cannot name: the `action` sentence alone, else the backend's
 * `detail`, else the generic line. `detail` ranks below `action` because it is
 * always English, and a translated "could not save the policy" serves a Korean
 * reader better than an English sentence that is merely more specific.
 */
export function problemMessage(
  err: unknown,
  t: TFunction,
  options: ProblemMessageOptions = {},
): string {
  const { prefix, action, allowDetailFallback = true } = options;
  const token = problemToken(err);
  const actionText = action ? t(action, { defaultValue: "" }) : "";

  const scoped = prefix
    ? t(`${prefix}.${token}`, { defaultValue: "" })
    : "";

  // A 422 is the one class where the backend knows something we cannot: which
  // field failed, which limit was exceeded, which statement in the uploaded
  // document was rejected. Its `detail` is English, and half an English
  // sentence is a real cost, but the alternative is telling the user only
  // that something was invalid, which leaves them with no way forward.
  if (!scoped && token === "validation" && err instanceof ProblemError && err.detail) {
    return actionText ? `${actionText} ${err.detail}` : err.detail;
  }
  const shared =
    token === "unknown" ? "" : t(COMMON_KEY_BY_TOKEN[token], { defaultValue: "" });
  const classText = scoped || shared;

  if (classText) {
    // The demo message already explains itself; prefixing it with "could not
    // save" would say the same thing twice.
    if (token === "demo_read_only") return classText;
    // Nor is there any point saying the same sentence twice, which happens
    // when a caller passes both a prefix and an action that resolve alike.
    if (!actionText || actionText === classText) return classText;
    return `${actionText} ${classText}`;
  }

  if (actionText) return actionText;

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
