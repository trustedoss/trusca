// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * useApiKeyScopes: which API-key scopes this user may issue (#136).
 *
 * Mirrors `_can_issue_at_scope` in `services/api_key_service.py`, which is
 * per-scope, not one global floor:
 *
 *   org      super_admin only.
 *   team     team_admin OF THAT TEAM.
 *   project  ANY member of the project's owning team.
 *
 * The page used to gate the whole entry point on `isTeamAdminOrAbove`, which
 * is right for two scopes out of three and wrong for the one the dialog
 * defaults to: a developer on a project's team is entitled to a project key
 * and had no way to ask for one.
 *
 * What the client can and cannot decide: membership comes from `/auth/me`, so
 * "belongs to at least one team" and "is team_admin of at least one team" are
 * answers we already hold. Whether a particular project or team id is one of
 * them is the server's call, and a wrong id still returns 403. These flags
 * only decide what to offer, never what to allow.
 */
import { usePermissions } from "@/hooks/usePermissions";
import { useAuthStore } from "@/stores/authStore";
import type { APIKeyScope } from "@/types/apiKey";

export interface ApiKeyScopePermissions {
  /** Scopes to offer, in the order the dialog lists them. */
  allowedScopes: APIKeyScope[];
  /** False when no scope is available, which makes the entry point pointless. */
  canIssueAnyKey: boolean;
}

export function useApiKeyScopes(): ApiKeyScopePermissions {
  const { isSuperAdmin } = usePermissions();
  const teams = useAuthStore((s) => s.user?.teams) ?? [];

  const allowedScopes: APIKeyScope[] = [];
  if (isSuperAdmin || teams.length > 0) allowedScopes.push("project");
  if (isSuperAdmin || teams.some((team) => team.role === "team_admin")) {
    allowedScopes.push("team");
  }
  if (isSuperAdmin) allowedScopes.push("org");

  return { allowedScopes, canIssueAnyKey: allowedScopes.length > 0 };
}
