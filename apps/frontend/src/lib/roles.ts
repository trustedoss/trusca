/**
 * Role promotion — single source for "what is this user's effective role?"
 * (H-2).
 *
 * `/auth/me` carries no top-level role: `is_superuser` is a boolean and the
 * team-scoped roles live on `memberships[].role`. The effective (global) role
 * promotes the HIGHEST membership role, so a team_admin is no longer treated
 * as a developer across the app chrome (approvals drawer, admin nav, ⌘K).
 *
 * Project-scoped surfaces (vuln triage, waivers, VEX import) must keep using
 * the server-computed `current_user_role` from the project overview — the
 * global role is deliberately NOT a substitute for per-team authority.
 */
import type { AuthRole } from "@/stores/authStore";

const ROLE_RANK: Record<AuthRole, number> = {
  developer: 0,
  team_admin: 1,
  super_admin: 2,
};

function isAuthRole(value: string): value is AuthRole {
  return value in ROLE_RANK;
}

/**
 * Promote the highest membership role to the effective global role.
 *
 * Least-privilege fallbacks: no memberships / unknown role strings resolve to
 * `developer`. `isSuperuser` wins unconditionally.
 */
export function effectiveRole(
  isSuperuser: boolean,
  membershipRoles: readonly string[],
): AuthRole {
  if (isSuperuser) return "super_admin";
  let best: AuthRole = "developer";
  for (const raw of membershipRoles) {
    if (isAuthRole(raw) && ROLE_RANK[raw] > ROLE_RANK[best]) {
      best = raw;
    }
  }
  return best;
}

/** `true` when `role` carries at least `minimum`'s authority. */
export function roleAtLeast(role: AuthRole, minimum: AuthRole): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[minimum];
}
