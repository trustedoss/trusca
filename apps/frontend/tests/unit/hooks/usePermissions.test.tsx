/**
 * usePermissions matrix (H-2 guard).
 *
 * The hook consolidates the global role gates (admin nav / ⌘K / approvals
 * actions). The matrix pins each role's flags AND the least-privilege
 * anonymous fallback, so a regression back to "is_superuser-only" mapping
 * fails loudly here.
 */
import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { usePermissions } from "@/hooks/usePermissions";
import { type AuthRole, type AuthUser, useAuthStore } from "@/stores/authStore";

function makeUser(
  role: AuthRole,
  teams: { id: string; name: string; role: string }[] = [],
): AuthUser {
  return {
    id: "u-1",
    email: "u@example.com",
    displayName: "U",
    role,
    isActive: true,
    isSuperuser: role === "super_admin",
    teamId: teams[0]?.id ?? null,
    teams,
  };
}

function setUser(user: AuthUser | null) {
  useAuthStore.setState({
    user,
    status: user ? "authenticated" : "anonymous",
    isAuthenticated: user != null,
    accessToken: user ? "tok" : null,
  });
}

afterEach(() => {
  useAuthStore.getState().reset();
});

describe("usePermissions", () => {
  it.each([
    ["super_admin", true, true],
    ["team_admin", false, true],
    ["developer", false, false],
  ] as const)("%s → isSuperAdmin=%s isTeamAdminOrAbove=%s", (
    role,
    isSuperAdmin,
    isTeamAdminOrAbove,
  ) => {
    setUser(makeUser(role));
    const { result } = renderHook(() => usePermissions());
    expect(result.current.role).toBe(role);
    expect(result.current.isSuperAdmin).toBe(isSuperAdmin);
    expect(result.current.isTeamAdminOrAbove).toBe(isTeamAdminOrAbove);
  });

  it("anonymous user resolves to least privilege", () => {
    setUser(null);
    const { result } = renderHook(() => usePermissions());
    expect(result.current.role).toBe("developer");
    expect(result.current.isSuperAdmin).toBe(false);
    expect(result.current.isTeamAdminOrAbove).toBe(false);
    expect(result.current.roleForTeam("team-1")).toBe("developer");
  });

  it("promotes a team_admin membership even when user.role is stale (H-2)", () => {
    // Mirrors the pre-fix wire shape: top-level role flattened to developer
    // while memberships carry team_admin.
    setUser(
      makeUser("developer", [{ id: "team-1", name: "A", role: "team_admin" }]),
    );
    const { result } = renderHook(() => usePermissions());
    expect(result.current.role).toBe("team_admin");
    expect(result.current.isTeamAdminOrAbove).toBe(true);
  });

  it("roleForTeam is team-scoped; super_admin overrides everywhere", () => {
    setUser(
      makeUser("developer", [
        { id: "team-1", name: "A", role: "team_admin" },
        { id: "team-2", name: "B", role: "developer" },
      ]),
    );
    const { result } = renderHook(() => usePermissions());
    expect(result.current.roleForTeam("team-1")).toBe("team_admin");
    expect(result.current.roleForTeam("team-2")).toBe("developer");
    expect(result.current.roleForTeam("team-unknown")).toBe("developer");
    expect(result.current.roleForTeam(null)).toBe("developer");

    setUser(makeUser("super_admin"));
    const { result: admin } = renderHook(() => usePermissions());
    expect(admin.current.roleForTeam("anything")).toBe("super_admin");
  });
});
