/**
 * adminErrorMessageKey — translates ProblemError instances into the
 * appropriate `admin.errors.*` i18n key. Cover each branch of the mapping.
 */
import { describe, expect, it } from "vitest";

import { adminErrorMessageKey } from "@/features/admin/lib/adminErrorMessage";
import { ProblemError, type ProblemDetails } from "@/lib/problem";

function buildProblem(extras: Record<string, unknown>): ProblemDetails {
  return {
    type: "about:blank",
    title: "Invariant",
    status: 422,
    detail: "boom",
    ...extras,
  } as ProblemDetails;
}

function err(status: number, problemExtras: Record<string, unknown>) {
  return new ProblemError("boom", {
    status,
    title: "Invariant",
    detail: "boom",
    problem: buildProblem(problemExtras),
  });
}

describe("adminErrorMessageKey", () => {
  it.each([
    ["last_super_admin_protected", "admin.errors.last_super_admin_protected"],
    ["cannot_modify_self", "admin.errors.cannot_modify_self"],
    ["last_team_admin_protected", "admin.errors.last_team_admin_protected"],
    ["team_has_active_scans", "admin.errors.team_has_active_scans"],
    ["invalid_role_assignment", "admin.errors.invalid_role_assignment"],
  ])("maps extension %s to %s", (extension, expected) => {
    const result = adminErrorMessageKey(err(422, { [extension]: true }));
    expect(result).toBe(expected);
  });

  it("treats 409 without extensions as a slug conflict", () => {
    expect(adminErrorMessageKey(err(409, {}))).toBe(
      "admin.errors.slug_conflict",
    );
  });

  it("falls back to unknown for arbitrary errors", () => {
    expect(adminErrorMessageKey(new Error("plain"))).toBe(
      "admin.errors.unknown",
    );
  });

  it("falls back to unknown for ProblemError without recognized markers", () => {
    expect(adminErrorMessageKey(err(500, {}))).toBe("admin.errors.unknown");
  });

  it("ignores extensions that are explicitly false", () => {
    expect(
      adminErrorMessageKey(err(422, { last_super_admin_protected: false })),
    ).toBe("admin.errors.unknown");
  });
});
