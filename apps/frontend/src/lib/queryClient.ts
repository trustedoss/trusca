// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import {
  MutationCache,
  QueryClient,
  type MutationOptions,
} from "@tanstack/react-query";

import i18n from "@/lib/i18n";
import { ProblemError } from "@/lib/problem";
import { problemMessage } from "@/lib/problemMessage";
import { dispatchToast } from "@/lib/toastBus";
import { currentUserGeneration } from "@/lib/userScopedState";

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
 * One deliberate exclusion: 422 validation problems stay inline next to the
 * field (design-system feedback rule, never a toast the user might miss while
 * typing).
 *
 * The wording comes from `problemMessage`, which translates the error class
 * and only falls back to the backend's English `detail` when it has nothing
 * better. This handler used to prefer `detail` outright, which meant every
 * failed write in a Korean session answered in English.
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

  dispatchToast(problemMessage(error, i18n.t.bind(i18n)), {
    tone: "error",
    key: "mutation-error",
  });
}

/**
 * Keep a mutation that outlives its user from touching the next user's cache.
 *
 * Signing out clears the cache, but a mutation already waiting on the server
 * keeps going, and its `onSuccess` / `onError` / `onSettled` handlers write
 * the result (or the optimistic-update rollback, which is the previous
 * user's snapshot) into whatever cache exists when it returns. There are
 * dozens of such handlers, so rather than guard each one, the mutation
 * function itself is wrapped: when the signed-in user changed while it was
 * in flight, its promise never settles and none of those handlers run. The
 * component that started it is gone by then, and the server-side effect has
 * happened either way.
 */
export function guardMutationFn<
  T extends MutationOptions<unknown, unknown, unknown, unknown>,
>(
  options: T,
): T {
  const mutationFn = options.mutationFn;
  if (!mutationFn) return options;
  return {
    ...options,
    mutationFn: async (variables: unknown) => {
      const startedIn = currentUserGeneration();
      const never = () => new Promise<never>(() => {});
      let result: unknown;
      try {
        result = await mutationFn(variables);
      } catch (error) {
        // A failure is held back too: `onError` rolls optimistic updates
        // back by writing the previous user's snapshot.
        if (currentUserGeneration() !== startedIn) return never();
        throw error;
      }
      if (currentUserGeneration() !== startedIn) return never();
      return result;
    },
  };
}

export function createQueryClient() {
  const client = new QueryClient({
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
  // Installed by overriding `defaultMutationOptions`, which both
  // `useMutation` (MutationObserver) and `MutationCache.build` call. Options
  // it has already returned carry TanStack's internal `_defaulted` flag and
  // are passed through so the wrapper is not stacked. Both behaviours are
  // pinned in queryClientMutationGuard.test.tsx, so an upgrade that changes
  // either fails there rather than silently dropping the guard.
  //
  // Consequence for callers: a mutation that outlives its user never
  // settles, so no `finally` (or code after `await mutateAsync`) may rely on
  // running once the user has changed.
  const defaultMutationOptions = client.defaultMutationOptions.bind(client);
  client.defaultMutationOptions = ((options) => {
    if (options?._defaulted) return options;
    return guardMutationFn(defaultMutationOptions(options));
  }) as QueryClient["defaultMutationOptions"];
  return client;
}
