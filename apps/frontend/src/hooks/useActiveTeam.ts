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
 *   1. The stored choice, but only if it still names one of the user's
 *      memberships. A revoked membership must not keep scoping anything,
 *      and the stored id outlives the session.
 *   2. The `teamId` the API resolved (first membership, oldest first).
 *   3. The first membership, for shapes that carry teams but no default.
 *
 * Returns `null` for a user with no memberships — the seeded super admin,
 * for instance — so callers render nothing rather than a placeholder.
 */
export function useActiveTeam(): TeamMembership | null {
  const user = useAuthStore((s) => s.user);
  const storedTeamId = useUIStore((s) => s.activeTeamId);

  const teams = user?.teams ?? [];
  if (teams.length === 0) return null;

  return (
    teams.find((team) => team.id === storedTeamId) ??
    teams.find((team) => team.id === user?.teamId) ??
    teams[0]
  );
}
