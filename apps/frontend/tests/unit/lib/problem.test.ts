/**
 * problem.ts — RFC 7807 Problem Details parser tests.
 *
 * Covers the F10 schema-hardening work: known-extension whitelist + zod
 * validation + unknown-key primitive-only fallback. Without the hardening
 * a backend change that lands a sensitive shape into a Problem extension
 * would silently round-trip into the UI; the tests below pin the contract.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  KNOWN_PROBLEM_EXTENSION_KEYS,
  parseProblemBody,
  ProblemError,
} from "@/lib/problem";

const STATUS_FALLBACK = { status: 500, statusText: "Internal Server Error" };

describe("parseProblemBody — standard envelope", () => {
  it("returns a normalized ProblemDetails for a valid envelope", () => {
    const { problem, title, detail } = parseProblemBody(
      {
        type: "https://docs.trustedoss.io/errors/cannot-modify-self",
        title: "Cannot Modify Self",
        status: 422,
        detail: "cannot modify your own role",
        instance: "/v1/admin/users/abc/role",
      },
      STATUS_FALLBACK,
    );
    expect(problem).not.toBeNull();
    expect(problem?.type).toBe(
      "https://docs.trustedoss.io/errors/cannot-modify-self",
    );
    expect(problem?.title).toBe("Cannot Modify Self");
    expect(problem?.status).toBe(422);
    expect(problem?.detail).toBe("cannot modify your own role");
    expect(problem?.instance).toBe("/v1/admin/users/abc/role");
    expect(title).toBe("Cannot Modify Self");
    expect(detail).toBe("cannot modify your own role");
  });

  it("falls back to about:blank when type is missing or non-string", () => {
    const { problem } = parseProblemBody(
      { title: "Boom", status: 500, detail: "x" },
      STATUS_FALLBACK,
    );
    expect(problem?.type).toBe("about:blank");
  });

  it("returns null when the body is not an object", () => {
    expect(parseProblemBody("not an object", STATUS_FALLBACK).problem).toBeNull();
    expect(parseProblemBody(null, STATUS_FALLBACK).problem).toBeNull();
    expect(parseProblemBody(123, STATUS_FALLBACK).problem).toBeNull();
  });

  it("returns null when the body is an array (not an object envelope)", () => {
    expect(parseProblemBody([1, 2, 3], STATUS_FALLBACK).problem).toBeNull();
  });

  it("substitutes about:blank fallback when type field is missing", () => {
    const { problem } = parseProblemBody(
      { title: "x", status: 400, detail: "y" },
      STATUS_FALLBACK,
    );
    expect(problem?.type).toBe("about:blank");
  });
});

// ---------------------------------------------------------------------------
// F10 — extension whitelist + sanitization
// ---------------------------------------------------------------------------

describe("parseProblemBody — known extensions", () => {
  it("preserves last_super_admin_protected boolean", () => {
    const { problem } = parseProblemBody(
      {
        type: "about:blank",
        title: "Last Super Admin Protected",
        status: 422,
        detail: "...",
        last_super_admin_protected: true,
      },
      STATUS_FALLBACK,
    );
    expect(problem?.last_super_admin_protected).toBe(true);
  });

  it("preserves cannot_modify_self / team_has_active_scans flags", () => {
    const { problem } = parseProblemBody(
      {
        title: "boom",
        status: 422,
        detail: "x",
        cannot_modify_self: true,
        team_has_active_scans: false,
      },
      STATUS_FALLBACK,
    );
    expect(problem?.cannot_modify_self).toBe(true);
    expect(problem?.team_has_active_scans).toBe(false);
  });

  it("preserves team_id (F9 — team-not-found)", () => {
    const { problem } = parseProblemBody(
      {
        title: "Team Not Found",
        status: 422,
        detail: "team xxx does not exist",
        team_id: "11111111-1111-1111-1111-111111111111",
      },
      STATUS_FALLBACK,
    );
    expect(problem?.team_id).toBe("11111111-1111-1111-1111-111111111111");
  });

  it("preserves the validation_error errors array verbatim", () => {
    const { problem } = parseProblemBody(
      {
        title: "Validation Error",
        status: 422,
        detail: "...",
        errors: [
          {
            type: "string_too_short",
            loc: ["body", "password"],
            msg: "...",
            input: "<redacted>",
          },
        ],
      },
      STATUS_FALLBACK,
    );
    expect(Array.isArray(problem?.errors)).toBe(true);
  });

  it("drops a known extension that has the wrong type", () => {
    // last_super_admin_protected MUST be boolean — a string here is malformed.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { problem } = parseProblemBody(
      {
        title: "x",
        status: 422,
        detail: "y",
        last_super_admin_protected: "true", // string, not boolean
      },
      STATUS_FALLBACK,
    );
    // Dropped — the malformed value MUST NOT round-trip.
    expect(problem?.last_super_admin_protected).toBeUndefined();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

describe("parseProblemBody — unknown extensions (graceful fallback)", () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  });
  afterEach(() => {
    warnSpy.mockRestore();
  });

  it("preserves a primitive value under an unknown key", () => {
    const { problem } = parseProblemBody(
      {
        title: "x",
        status: 422,
        detail: "y",
        // This key isn't in KNOWN_PROBLEM_EXTENSION_KEYS yet.
        future_extension: "some-string",
      },
      STATUS_FALLBACK,
    );
    expect(problem?.future_extension).toBe("some-string");
  });

  it("preserves number / boolean / null primitives under unknown keys", () => {
    const { problem } = parseProblemBody(
      {
        title: "x",
        status: 422,
        detail: "y",
        future_count: 42,
        future_flag: true,
        future_nullish: null,
      },
      STATUS_FALLBACK,
    );
    expect(problem?.future_count).toBe(42);
    expect(problem?.future_flag).toBe(true);
    expect(problem?.future_nullish).toBeNull();
  });

  it("DROPS an unknown key whose value is a nested object (sensitive-shape guard)", () => {
    // Imagine a backend regression that accidentally exposes a stack trace
    // or internal config under an unknown extension key. We MUST NOT round-
    // trip the nested shape into the UI's error envelope.
    const { problem } = parseProblemBody(
      {
        title: "x",
        status: 500,
        detail: "y",
        debug_info: {
          stack: "at internalFn (/srv/...)",
          db_password: "leaked",
        },
      },
      STATUS_FALLBACK,
    );
    expect(problem?.debug_info).toBeUndefined();
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("dropping unknown non-primitive extension"),
    );
  });

  it("DROPS an unknown key whose value is an array (shape-leak guard)", () => {
    const { problem } = parseProblemBody(
      {
        title: "x",
        status: 500,
        detail: "y",
        future_array: [1, 2, 3],
      },
      STATUS_FALLBACK,
    );
    expect(problem?.future_array).toBeUndefined();
  });
});

describe("parseProblemBody — graceful malformed inputs", () => {
  it("non-string type field defaults to about:blank", () => {
    const { problem } = parseProblemBody(
      { type: 123, title: "x", status: 400, detail: "y" },
      STATUS_FALLBACK,
    );
    expect(problem?.type).toBe("about:blank");
  });

  it("missing title falls back to fallback statusText", () => {
    const { problem, title } = parseProblemBody(
      { detail: "...", status: 500 },
      STATUS_FALLBACK,
    );
    // statusText comes from STATUS_FALLBACK = "Internal Server Error".
    expect(title).toBe("Internal Server Error");
    expect(problem?.title).toBe("Internal Server Error");
  });

  it("non-number status field defaults to fallback status", () => {
    const { problem } = parseProblemBody(
      { type: "x", title: "y", status: "not a number", detail: "z" },
      STATUS_FALLBACK,
    );
    expect(problem?.status).toBe(STATUS_FALLBACK.status);
  });

  it("detail falls back to title when omitted", () => {
    const { detail } = parseProblemBody(
      { title: "Boom", status: 500 },
      STATUS_FALLBACK,
    );
    expect(detail).toBe("Boom");
  });
});

describe("ProblemError class", () => {
  it("carries status / title / detail / problem references", () => {
    const problem = {
      type: "about:blank",
      title: "Boom",
      status: 500,
      detail: "x",
    };
    const err = new ProblemError("Boom", {
      status: 500,
      title: "Boom",
      detail: "x",
      problem,
    });
    expect(err.name).toBe("ProblemError");
    expect(err.status).toBe(500);
    expect(err.problem).toBe(problem);
  });
});

describe("KNOWN_PROBLEM_EXTENSION_KEYS — module export pin", () => {
  it("includes every domain extension currently used by the backend", () => {
    // Pin so a backend rename (e.g. `last_super_admin_protected` →
    // `last_super_admin_lock`) without a frontend whitelist update fires
    // a CI failure visible in code review.
    expect(KNOWN_PROBLEM_EXTENSION_KEYS).toEqual(
      expect.arrayContaining([
        "last_super_admin_protected",
        "cannot_modify_self",
        // "invalid_role_assignment" removed — L1: backend never emits this as
        // an extension boolean flag; removed from the whitelist in chore PR #9.
        "team_has_active_scans",
        "last_team_admin_protected",
        "team_id",
      ]),
    );
  });

  it("includes every PR #14 admin operational extension", () => {
    expect(KNOWN_PROBLEM_EXTENSION_KEYS).toEqual(
      expect.arrayContaining([
        "dt_unreachable",
        "dt_orphan_cleanup_in_progress",
        "scan_already_cancelled",
        "scan_not_found",
        // "disk_path_unavailable" removed — L1: backend never emits this as
        // an extension boolean flag; removed from the whitelist in chore PR #9.
        "audit_export_too_large",
      ]),
    );
  });
});

describe("parseProblemBody — PR #14 admin extensions", () => {
  it("preserves dt_unreachable boolean", () => {
    const { problem } = parseProblemBody(
      {
        type: "about:blank",
        title: "DT unreachable",
        status: 503,
        detail: "breaker is open",
        dt_unreachable: true,
      },
      STATUS_FALLBACK,
    );
    expect(problem?.dt_unreachable).toBe(true);
  });

  it("preserves scan_already_cancelled boolean", () => {
    const { problem } = parseProblemBody(
      {
        type: "about:blank",
        title: "scan already cancelled",
        status: 409,
        detail: "scan already cancelled",
        scan_already_cancelled: true,
      },
      STATUS_FALLBACK,
    );
    expect(problem?.scan_already_cancelled).toBe(true);
  });

  it("preserves audit_export_too_large boolean", () => {
    const { problem } = parseProblemBody(
      {
        type: "about:blank",
        title: "export too large",
        status: 413,
        detail: "narrow window",
        audit_export_too_large: true,
      },
      STATUS_FALLBACK,
    );
    expect(problem?.audit_export_too_large).toBe(true);
  });

  it("drops a malformed scan_not_found (non-boolean)", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { problem } = parseProblemBody(
      {
        type: "about:blank",
        title: "scan not found",
        status: 404,
        detail: "missing",
        scan_not_found: "yes",
      },
      STATUS_FALLBACK,
    );
    expect(problem?.scan_not_found).toBeUndefined();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});
