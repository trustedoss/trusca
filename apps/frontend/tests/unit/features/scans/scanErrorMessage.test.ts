/**
 * scanErrorMessage — unit tests (PR-A3).
 *
 * The cancel endpoint's failure modes must map to stable i18n keys + tokens
 * regardless of locale. Extension flags take precedence over the raw HTTP
 * status (a 409 without the flag still falls back to "already terminal", but
 * the flag is the contract).
 */
import { describe, expect, it } from "vitest";

import {
  scanCancelErrorKey,
  scanCancelErrorToken,
} from "@/features/scans/scanErrorMessage";
import { ProblemError } from "@/lib/problem";

function problem(
  status: number,
  extension?: Record<string, unknown>,
): ProblemError {
  return new ProblemError("err", {
    status,
    title: "t",
    detail: "d",
    problem: {
      type: "about:blank",
      title: "t",
      status,
      detail: "d",
      ...extension,
    },
  });
}

describe("scanCancelErrorKey / scanCancelErrorToken", () => {
  it("maps scan_already_cancelled extension", () => {
    const err = problem(409, { scan_already_cancelled: true });
    expect(scanCancelErrorKey(err)).toBe("cancel.errors.already_terminal");
    expect(scanCancelErrorToken(err)).toBe("scan_already_cancelled");
  });

  it("maps scan_not_found extension", () => {
    const err = problem(404, { scan_not_found: true });
    expect(scanCancelErrorKey(err)).toBe("cancel.errors.not_found");
    expect(scanCancelErrorToken(err)).toBe("scan_not_found");
  });

  it("falls back to status family when no extension present", () => {
    expect(scanCancelErrorKey(problem(409))).toBe(
      "cancel.errors.already_terminal",
    );
    expect(scanCancelErrorKey(problem(404))).toBe("cancel.errors.not_found");
    expect(scanCancelErrorToken(problem(403))).toBe("forbidden");
    expect(scanCancelErrorKey(problem(403))).toBe("cancel.errors.forbidden");
  });

  it("falls back to unknown for non-ProblemError and unmapped status", () => {
    expect(scanCancelErrorKey(new Error("boom"))).toBe(
      "cancel.errors.unknown",
    );
    expect(scanCancelErrorToken(new Error("boom"))).toBe("unknown");
    expect(scanCancelErrorKey(problem(500))).toBe("cancel.errors.unknown");
    expect(scanCancelErrorToken(problem(500))).toBe("unknown");
  });
});
