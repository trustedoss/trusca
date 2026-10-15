/**
 * demoReadOnly classifier + error-mapper integration — v2.1 Track B (B5).
 *
 * The public read-only demo blocks writes with a 403 + Problem Details
 * (`type: urn:trustedoss:problem:demo-read-only`, `demo_read_only: true`).
 * Because the demo middleware runs BEFORE auth, that 403 must NOT be mapped to
 * the generic "forbidden / permission denied" copy — it has to read as "this is
 * a read-only demo". These tests pin that distinction across:
 *   - the shared `isDemoReadOnlyError` classifier,
 *   - the admin write-error mapper (`adminErrorMessageKey` / `adminErrorExtension`),
 *   - the project surface mapper (`projectErrorToken` / `projectErrorMessageKey`),
 * and confirm `parseProblemBody` whitelists the `demo_read_only` extension.
 */
import { describe, expect, it } from "vitest";

import {
  adminErrorExtension,
  adminErrorMessageKey,
} from "@/features/admin/lib/adminErrorMessage";
import {
  DEMO_READ_ONLY_MESSAGE_KEY,
  projectErrorMessageKey,
  projectErrorToken,
} from "@/features/projects/lib/projectErrorMessage";
import {
  DEMO_READ_ONLY_PROBLEM_TYPE,
  isDemoAdminHiddenError,
  isDemoReadOnlyError,
} from "@/lib/demoReadOnly";
import {
  KNOWN_PROBLEM_EXTENSION_KEYS,
  parseProblemBody,
  ProblemError,
  type ProblemDetails,
} from "@/lib/problem";

/** A 403 ProblemError shaped exactly like the demo middleware's response. */
function demoError(): ProblemError {
  const problem: ProblemDetails = {
    type: DEMO_READ_ONLY_PROBLEM_TYPE,
    title: "Read-only demo",
    status: 403,
    detail: "This deployment is a read-only demo. Writes are disabled.",
    demo_read_only: true,
  };
  return new ProblemError("Read-only demo", {
    status: 403,
    title: "Read-only demo",
    detail: problem.detail,
    problem,
  });
}

/** A plain permission-denied 403 (NOT the demo guard). */
function forbiddenError(): ProblemError {
  const problem: ProblemDetails = {
    type: "about:blank",
    title: "Forbidden",
    status: 403,
    detail: "You do not have access to this resource.",
  };
  return new ProblemError("Forbidden", {
    status: 403,
    title: "Forbidden",
    detail: problem.detail,
    problem,
  });
}

describe("parseProblemBody — demo_read_only extension", () => {
  it("whitelists demo_read_only", () => {
    expect(KNOWN_PROBLEM_EXTENSION_KEYS).toContain("demo_read_only");
  });

  it("preserves the demo_read_only boolean from the middleware response", () => {
    const { problem } = parseProblemBody(
      {
        type: DEMO_READ_ONLY_PROBLEM_TYPE,
        title: "Read-only demo",
        status: 403,
        detail: "writes disabled",
        demo_read_only: true,
      },
      { status: 403, statusText: "Forbidden" },
    );
    expect(problem?.demo_read_only).toBe(true);
    expect(problem?.type).toBe(DEMO_READ_ONLY_PROBLEM_TYPE);
  });
});

describe("isDemoReadOnlyError", () => {
  it("matches the demo middleware 403 (by type and by extension flag)", () => {
    expect(isDemoReadOnlyError(demoError())).toBe(true);
  });

  it("matches when only the extension flag is present", () => {
    const problem: ProblemDetails = {
      type: "about:blank",
      title: "Read-only demo",
      status: 403,
      detail: "x",
      demo_read_only: true,
    };
    const err = new ProblemError("x", {
      status: 403,
      title: "Read-only demo",
      detail: "x",
      problem,
    });
    expect(isDemoReadOnlyError(err)).toBe(true);
  });

  it("does NOT match an ordinary permission-denied 403", () => {
    expect(isDemoReadOnlyError(forbiddenError())).toBe(false);
  });

  it("does NOT match non-Problem errors", () => {
    expect(isDemoReadOnlyError(new Error("plain"))).toBe(false);
    expect(isDemoReadOnlyError(null)).toBe(false);
    expect(isDemoReadOnlyError(undefined)).toBe(false);
  });
});

describe("adminErrorMessageKey / adminErrorExtension — demo branch wins", () => {
  it("maps the demo 403 to admin.errors.demo_read_only (not forbidden/unknown)", () => {
    expect(adminErrorMessageKey(demoError())).toBe(
      "admin.errors.demo_read_only",
    );
    expect(adminErrorExtension(demoError())).toBe("demo_read_only");
  });

  it("still maps an ordinary 403 to the generic fallback (distinct from demo)", () => {
    // A plain 403 is not a known admin extension/409, so it falls through to
    // unknown — the point is it is NOT the demo key.
    expect(adminErrorMessageKey(forbiddenError())).toBe("admin.errors.unknown");
    expect(adminErrorMessageKey(forbiddenError())).not.toBe(
      "admin.errors.demo_read_only",
    );
  });
});

describe("projectErrorToken / projectErrorMessageKey — demo branch wins", () => {
  it("classifies the demo 403 as demo_read_only, not forbidden", () => {
    expect(projectErrorToken(demoError())).toBe("demo_read_only");
    expect(projectErrorToken(forbiddenError())).toBe("forbidden");
  });

  it("resolves the demo case to the shared common key (no per-prefix subkey)", () => {
    expect(projectErrorMessageKey(demoError(), "page.errors")).toBe(
      DEMO_READ_ONLY_MESSAGE_KEY,
    );
    // An ordinary 403 keeps the per-prefix forbidden key.
    expect(projectErrorMessageKey(forbiddenError(), "page.errors")).toBe(
      "page.errors.forbidden",
    );
  });
});

/** The demo middleware's refusal of an admin READ (a hidden admin screen). */
function demoAdminHiddenError(): ProblemError {
  const { problem } = parseProblemBody(
    {
      type: DEMO_READ_ONLY_PROBLEM_TYPE,
      title: "Not available in the demo",
      status: 403,
      detail: "This administration screen is not available in the public live demo.",
      instance: "/v1/admin/backup",
      demo_read_only: true,
      demo_admin_hidden: true,
    },
    { status: 403, statusText: "Forbidden" },
  );
  return new ProblemError("Not available in the demo", {
    status: 403,
    title: "Not available in the demo",
    detail: "hidden",
    problem,
  });
}

describe("demo admin-hidden refusal", () => {
  it("parseProblemBody keeps the demo_admin_hidden flag", () => {
    expect(KNOWN_PROBLEM_EXTENSION_KEYS).toContain("demo_admin_hidden");
    const err = demoAdminHiddenError();
    expect(
      (err.problem as unknown as Record<string, unknown>).demo_admin_hidden,
    ).toBe(true);
  });

  it("is classified as admin-hidden and still as a demo refusal", () => {
    expect(isDemoAdminHiddenError(demoAdminHiddenError())).toBe(true);
    expect(isDemoReadOnlyError(demoAdminHiddenError())).toBe(true);
  });

  it("a demo WRITE refusal is not admin-hidden", () => {
    expect(isDemoAdminHiddenError(demoError())).toBe(false);
    expect(isDemoAdminHiddenError(forbiddenError())).toBe(false);
    expect(isDemoAdminHiddenError(new Error("x"))).toBe(false);
  });

  it("maps to its own admin message, not the write message", () => {
    expect(adminErrorMessageKey(demoAdminHiddenError())).toBe(
      "admin.errors.demo_admin_hidden",
    );
    expect(adminErrorExtension(demoAdminHiddenError())).toBe("demo_admin_hidden");
    // The write refusal keeps its own key.
    expect(adminErrorMessageKey(demoError())).toBe("admin.errors.demo_read_only");
  });
});
