/**
 * policyErrorMessage — unit tests (v2.2 c3).
 *
 * Maps each license-policy HTTP failure to a stable token + i18n key. The c1
 * backend keys failures by status (403/404/409/422), so the mapper is a pure
 * status switch with an "unknown" fallback for non-ProblemError throws.
 */
import { describe, expect, it } from "vitest";

import {
  policyErrorMessageKey,
  policyErrorToken,
} from "@/features/policies/policyErrorMessage";
import { ProblemError } from "@/lib/problem";

function problem(status: number): ProblemError {
  return new ProblemError("x", {
    status,
    title: "t",
    detail: "d",
    problem: { type: "about:blank", title: "t", status, detail: "d" },
  });
}

describe("policyErrorMessage", () => {
  it.each([
    [403, "forbidden"],
    [404, "not_found"],
    [409, "conflict"],
    [422, "validation"],
    [500, "unknown"],
  ])("maps status %i to token %s", (status, token) => {
    expect(policyErrorToken(problem(status))).toBe(token);
    expect(policyErrorMessageKey(problem(status))).toBe(
      `policies.errors.${token}`,
    );
  });

  it("falls back to unknown for a non-ProblemError throw", () => {
    expect(policyErrorToken(new Error("boom"))).toBe("unknown");
    expect(policyErrorMessageKey("not even an error")).toBe(
      "policies.errors.unknown",
    );
  });
});
