// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import type { QueryClient } from "@tanstack/react-query";

import { useAuthStore } from "@/stores/authStore";
import { useUIStore } from "@/stores/uiStore";

/**
 * Bumped every time the signed-in user's state is dropped. Work that started
 * under one generation (a mutation awaiting its response, say) compares it
 * when it finishes and stays out of a cache that now belongs to someone else.
 */
let generation = 0;

export function currentUserGeneration(): number {
  return generation;
}

/**
 * Drop everything that belongs to the signed-in user whenever that user goes
 * away or is replaced.
 *
 * Query keys carry no user identity, so a cached `["projects"]` entry fetched
 * by one account would otherwise be served to the next account signed in to
 * the same tab until it went stale. Rather than ask every sign-out path to
 * remember this, the binding watches the one thing they all change: the
 * auth store's `user`. Voluntary sign-out (`logout()`), the forced sign-out
 * after a failed refresh (`reset()` in the axios interceptor and in
 * `AuthExpiredListener`), a sign-out or sign-in in another tab (see
 * `authBroadcast`), and a sign-in that replaces one user with another
 * (`setUser`) all pass through it.
 *
 * The first sign-in of a page (no user to a user) keeps the query cache:
 * there is no earlier user in this tab, and the only queries that can exist
 * then are the public ones the sign-in screen itself runs. The acting team is
 * different, because it is persisted and may have been chosen by whoever used
 * this browser last. It is kept only when it was chosen by the same user.
 *
 * Returns the unsubscribe function.
 */
export function bindUserScopedState(queryClient: QueryClient): () => void {
  // A page that loads signed out keeps the persisted team for now: this may
  // be a reload, and the user who signs in next may be the one who chose it.
  const current = useAuthStore.getState().user?.id ?? null;
  if (current !== null) claimActiveTeam(current);
  return useAuthStore.subscribe((state, prev) => {
    const previousId = prev.user?.id ?? null;
    const nextId = state.user?.id ?? null;
    if (previousId === nextId) return;
    if (previousId !== null) {
      generation += 1;
      // `clear()` also cancels in-flight fetches, so a response that arrives
      // after sign-out cannot write the previous user's data back in.
      queryClient.clear();
    }
    claimActiveTeam(nextId);
  });
}

/**
 * Keep the persisted acting team only if `userId` chose it, then record
 * `userId` as its owner. A team saved before owners were recorded has no
 * owner and is dropped once.
 */
function claimActiveTeam(userId: string | null): void {
  const ui = useUIStore.getState();
  if (ui.activeTeamOwnerId === userId) return;
  if (ui.activeTeamOwnerId !== null || ui.activeTeamId !== null) {
    ui.setActiveTeamId(null);
  }
  useUIStore.setState({ activeTeamOwnerId: userId });
}
