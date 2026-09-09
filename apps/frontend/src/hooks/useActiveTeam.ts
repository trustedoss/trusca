// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useAuthStore, type TeamMembership } from "@/stores/authStore";
import { useUIStore } from "@/stores/uiStore";

/**
 * The team the user is currently acting as.
 *
 * One place resolves this, because the global bar and the project-creation
 * form both need the answer and had no way to agree on it: the form read
 * the auth store once, in a `useState` initialiser, so switching teams in
 * the bar left the two controls contradicting each other — the bar saying
 * "Security", the form still submitting Platform.
 *
 * Resolution order:
 *   1. The stored choice, if it still names one of the user's memberships.
 *   2. If a choice IS stored but names something else, a group reachable
 *      only through the group-hierarchy cascade (Phase 4 PR 4-B security
 *      review: `GroupDetailPage`'s "New project" button stores the clicked
 *      group's id via `setActiveTeamId` unconditionally, whether or not the
 *      user holds a direct membership there), `null`, not a fallback to
 *      some OTHER team. Falling through here used to mean a stale, unrelated
 *      membership got silently substituted: the user picks group C, the
 *      form quietly resolves to group A instead with no signal anywhere
 *      that a substitution happened. `null` is not "no active team" in this
 *      branch; callers that need to tell the two apart check
 *      `user.teams.length > 0` alongside a `null` result (see
 *      `ProjectCreatePage` for the write path this actually matters for).
 *   3. No choice stored at all: the `teamId` the API resolved (first
 *      membership, oldest first).
 *   4. The first membership, for shapes that carry teams but no default.
 *
 * Returns `null` for a user with no memberships at all (the seeded super
 * admin, for instance), so callers render nothing rather than a
 * placeholder; see point 2 above for the OTHER, newer case this now also
 * returns `null` for.
 */
export function useActiveTeam(): TeamMembership | null {
  const user = useAuthStore((s) => s.user);
  const storedTeamId = useUIStore((s) => s.activeTeamId);

  const teams = user?.teams ?? [];
  if (teams.length === 0) return null;

  if (storedTeamId !== null) {
    return teams.find((team) => team.id === storedTeamId) ?? null;
  }

  return (
    teams.find((team) => team.id === user?.teamId) ??
    teams[0]
  );
}
