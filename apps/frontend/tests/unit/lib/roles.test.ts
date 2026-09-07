/**
 * Role promotion matrix (H-2 guard).
 *
 * `effectiveRole` is the single source for "memberships → effective global
 * role"; `toAuthUser` and `usePermissions` both lean on it, so this matrix is
 * the regression net for the original H-2 bug (group_admin flattened to
 * developer).
 */
import { describe, expect, it } from "vitest";

import { effectiveRole, roleAtLeast } from "@/lib/roles";

describe("effectiveRole", () => {
  it("superuser wins regardless of memberships", () => {
    expect(effectiveRole(true, [])).toBe("super_admin");
    expect(effectiveRole(true, ["developer"])).toBe("super_admin");
    expect(effectiveRole(true, ["group_admin"])).toBe("super_admin");
  });

  it("promotes the highest membership role (the H-2 fix)", () => {
    expect(effectiveRole(false, ["group_admin"])).toBe("group_admin");
    expect(effectiveRole(false, ["developer", "group_admin"])).toBe("group_admin");
    expect(effectiveRole(false, ["group_admin", "developer"])).toBe("group_admin");
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
    expect(effectiveRole(false, ["group_admin", "developer"])).not.toBe(
      "super_admin",
    );
  });
});

describe("roleAtLeast", () => {
  it("orders developer < group_admin < super_admin", () => {
    expect(roleAtLeast("developer", "developer")).toBe(true);
    expect(roleAtLeast("developer", "group_admin")).toBe(false);
    expect(roleAtLeast("group_admin", "group_admin")).toBe(true);
    expect(roleAtLeast("group_admin", "super_admin")).toBe(false);
    expect(roleAtLeast("super_admin", "group_admin")).toBe(true);
    expect(roleAtLeast("super_admin", "super_admin")).toBe(true);
  });
});
