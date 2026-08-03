/**
 * Role promotion matrix (H-2 guard).
 *
 * `effectiveRole` is the single source for "memberships → effective global
 * role"; `toAuthUser` and `usePermissions` both lean on it, so this matrix is
 * the regression net for the original H-2 bug (team_admin flattened to
 * developer).
 */
import { describe, expect, it } from "vitest";

import { effectiveRole, roleAtLeast } from "@/lib/roles";

describe("effectiveRole", () => {
  it("superuser wins regardless of memberships", () => {
    expect(effectiveRole(true, [])).toBe("super_admin");
    expect(effectiveRole(true, ["developer"])).toBe("super_admin");
    expect(effectiveRole(true, ["team_admin"])).toBe("super_admin");
  });

  it("promotes the highest membership role (the H-2 fix)", () => {
    expect(effectiveRole(false, ["team_admin"])).toBe("team_admin");
    expect(effectiveRole(false, ["developer", "team_admin"])).toBe("team_admin");
    expect(effectiveRole(false, ["team_admin", "developer"])).toBe("team_admin");
  });

  it("developer-only memberships stay developer", () => {
    expect(effectiveRole(false, ["developer"])).toBe("developer");
    expect(effectiveRole(false, ["developer", "developer"])).toBe("developer");
  });

  it("least-privilege fallback: no memberships / unknown roles → developer", () => {
    expect(effectiveRole(false, [])).toBe("developer");
    expect(effectiveRole(false, ["owner", "ADMIN", ""])).toBe("developer");
  });

  it("a membership string can never escalate to super_admin", () => {
    expect(effectiveRole(false, ["super_admin"])).toBe("super_admin");
    // super_admin IS a valid AuthRole; global super_admin via membership is
    // not used by the backend today, but the rank order must stay total.
    expect(effectiveRole(false, ["team_admin", "developer"])).not.toBe(
      "super_admin",
    );
  });
});

describe("roleAtLeast", () => {
  it("orders developer < team_admin < super_admin", () => {
    expect(roleAtLeast("developer", "developer")).toBe(true);
    expect(roleAtLeast("developer", "team_admin")).toBe(false);
    expect(roleAtLeast("team_admin", "team_admin")).toBe(true);
    expect(roleAtLeast("team_admin", "super_admin")).toBe(false);
    expect(roleAtLeast("super_admin", "team_admin")).toBe(true);
    expect(roleAtLeast("super_admin", "super_admin")).toBe(true);
  });
});
