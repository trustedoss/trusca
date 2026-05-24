/**
 * projectErrorMessage — unit tests (BUG-002).
 *
 * Verifies the RFC 7807 → i18n-key mapping the project surfaces use to avoid
 * leaking the backend's English `title`/`detail` into the KO locale.
 */
import { describe, expect, it } from "vitest";

import {
  projectErrorMessageKey,
  projectErrorToken,
} from "@/features/projects/lib/projectErrorMessage";
import { ProblemError } from "@/lib/problem";

function problem(status: number): ProblemError {
  return new ProblemError("boom", {
    status,
    title: "English Title",
    detail: "english detail that must not leak",
    problem: null,
  });
}

describe("projectErrorToken", () => {
  it("maps 404 → not_found", () => {
    expect(projectErrorToken(problem(404))).toBe("not_found");
  });

  it("maps 403 → forbidden", () => {
    expect(projectErrorToken(problem(403))).toBe("forbidden");
  });

  it("maps other statuses → unknown", () => {
    expect(projectErrorToken(problem(500))).toBe("unknown");
    expect(projectErrorToken(problem(409))).toBe("unknown");
  });

  it("maps a non-Problem error → unknown", () => {
    expect(projectErrorToken(new Error("network"))).toBe("unknown");
    expect(projectErrorToken(null)).toBe("unknown");
    expect(projectErrorToken(undefined)).toBe("unknown");
  });
});

describe("projectErrorMessageKey", () => {
  it("prefixes the token with the caller namespace", () => {
    expect(projectErrorMessageKey(problem(404), "page.errors")).toBe(
      "page.errors.not_found",
    );
    expect(
      projectErrorMessageKey(problem(403), "overview.gate_card.errors"),
    ).toBe("overview.gate_card.errors.forbidden");
    expect(projectErrorMessageKey(new Error("x"), "page.errors")).toBe(
      "page.errors.unknown",
    );
  });
});
