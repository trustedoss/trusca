// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * usePermissions — the one hook for global role gates (H-2).
 *
 * Consolidates the `user?.isSuperuser === true || user?.role === "super_admin"`
 * idiom that was copy-pasted across AdminLayout / PoliciesPage / CommandMenu /
 * ApprovalsDrawer. Reads the auth store; everything is derived, so it stays
 * in sync with login / logout / bootstrap.
 *
 * Least privilege: an anonymous (null) user resolves to `developer` with every
 * flag false — callers never need their own null guard.
 *
 * Scope note: these are GLOBAL gates (app chrome, admin nav, approvals
 * actions). Project-scoped decisions (vuln triage, waivers) must keep using
 * the server-computed `current_user_role` from the project overview.
 */
import { effectiveRole, roleAtLeast } from "@/lib/roles";
import { type AuthRole, useAuthStore } from "@/stores/authStore";

export interface Permissions {
  /** Effective global role (memberships promoted; `developer` fallback). */
  role: AuthRole;
  isSuperAdmin: boolean;
  /** team_admin of ANY team, or super_admin. */
  isTeamAdminOrAbove: boolean;
  /** Team-scoped role for one team id (super_admin overrides everywhere). */
  roleForTeam: (teamId: string | null | undefined) => AuthRole;
}

export function usePermissions(): Permissions {
  const user = useAuthStore((s) => s.user);

  // No user means no grade, and the lowest grade is the honest answer. This
  // read "developer" until a grade existed below it, at which point the
  // fallback stopped meaning least privilege and started meaning "assume they
  // can run scans".
  const role: AuthRole = user
    ? effectiveRole(
        user.isSuperuser || user.role === "super_admin",
        [user.role, ...(user.teams ?? []).map((t) => t.role)],
      )
    : "viewer";

  return {
    role,
    isSuperAdmin: role === "super_admin",
    isTeamAdminOrAbove: roleAtLeast(role, "team_admin"),
    roleForTeam: (teamId) => {
      if (role === "super_admin") return "super_admin";
      // Not a member of this team, so nothing about it is theirs to do. The
      // server refuses either way; showing affordances it will refuse is the
      // failure this avoids.
      if (!teamId) return "viewer";
      const membership = (user?.teams ?? []).find((t) => t.id === teamId);
      return membership ? effectiveRole(false, [membership.role]) : "viewer";
    },
  };
}
