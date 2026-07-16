import { MutationCache, QueryClient } from "@tanstack/react-query";

import i18n from "@/lib/i18n";
import { ProblemError } from "@/lib/problem";
import { dispatchToast } from "@/lib/toastBus";

/**
 * Mutation meta contract for the global error toast (see `onMutationError`).
 *
 *   - `errorToast: false` — this mutation surfaces its error inline (e.g. an
 *     Alert next to the form) and the global toast must stay quiet.
 *   - `errorToast: true`  — force the toast even though the mutation defines
 *     its own `onError` (useful when the local handler only rolls back cache
 *     state and shows nothing).
 *   - unset — the toast fires exactly when the mutation does NOT define its
 *     own `onError` (a local handler is presumed to own the error UX).
 */
declare module "@tanstack/react-query" {
  interface Register {
    mutationMeta: {
      errorToast?: boolean;
    };
  }
}

/**
 * Global mutation error handler — no failed write may stay silent.
 *
 * Before this, error feedback was strictly per-call-site: mutations without
 * an `onError`/catch swallowed failures and the user saw nothing (the audit
 * that motivated this found several silent writes). The cache-level handler
 * is the safety net; call sites that already render errors keep doing so and
 * opt out via the meta contract above.
 *
 * Two deliberate exclusions:
 *   - 422 validation problems stay inline next to the field (design-system
 *     feedback rule — never a toast the user might miss while typing).
 *   - `ProblemError.detail` is preferred over a generic message because the
 *     backend's RFC 7807 `detail` is always populated and user-readable.
 */
export function onMutationError(
  error: unknown,
  mutation: {
    options: { onError?: unknown };
    meta?: { errorToast?: boolean } | undefined;
  },
): void {
  const forced = mutation.meta?.errorToast === true;
  if (mutation.meta?.errorToast === false) return;
  if (mutation.options.onError && !forced) return;
  if (error instanceof ProblemError && error.status === 422 && !forced) return;

  const text =
    error instanceof ProblemError && error.detail
      ? error.detail
      : i18n.t("common:errors.request_failed");
  dispatchToast(text, { tone: "error", key: "mutation-error" });
}

export function createQueryClient() {
  return new QueryClient({
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) =>
        onMutationError(error, mutation),
    }),
    defaultOptions: {
      queries: {
        // Server state defaults: refetch on focus is a UX trap on long scan
        // dashboards. Components that need it can opt back in per query.
        refetchOnWindowFocus: false,
        retry: 1,
        staleTime: 30_000,
      },
    },
  });
}
