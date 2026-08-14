/**
 * problemMessage — unit tests.
 *
 * The behaviour worth pinning is the resolution order, because the defect it
 * replaces was an order problem: the backend's English `detail` outranked the
 * translation that was sitting right there. So these assert what wins, in both
 * locales, rather than just that some string comes back.
 */
import { describe, expect, it } from "vitest";

import i18n from "@/lib/i18n";
import { ProblemError } from "@/lib/problem";
import { problemMessage, problemToken } from "@/lib/problemMessage";

function problem(
  status: number,
  detail = "Something specific in English",
  extensions: Record<string, unknown> = {},
): ProblemError {
  return new ProblemError(detail, {
    status,
    title: "Title",
    detail,
    problem: {
      type: "about:blank",
      title: "Title",
      status,
      detail,
      ...extensions,
    },
  });
}

const en = i18n.getFixedT("en");
const ko = i18n.getFixedT("ko");

describe("problemToken", () => {
  it.each([
    [0, "network"],
    [401, "unauthorized"],
    [403, "forbidden"],
    [404, "not_found"],
    [409, "conflict"],
    [412, "conflict"],
    [429, "rate_limited"],
    [500, "server_error"],
    [503, "server_error"],
    [418, "unknown"],
  ])("classifies status %i as %s", (status, expected) => {
    expect(problemToken(problem(status))).toBe(expected);
  });

  it("classifies a non-Problem error as unknown", () => {
    expect(problemToken(new Error("boom"))).toBe("unknown");
    expect(problemToken(null)).toBe("unknown");
  });

  it("checks the read-only demo before the permission denial", () => {
    // The demo guard runs ahead of auth and answers 403. Classifying by status
    // first would report a blocked demo write as "you lack permission", which
    // sends the user looking for an admin who cannot help.
    expect(problemToken(problem(403, "read-only", { demo_read_only: true }))).toBe(
      "demo_read_only",
    );
  });
});

describe("problemMessage", () => {
  it("translates the class instead of echoing the backend's English", () => {
    const err = problem(404, "project 7f3 not found");

    expect(problemMessage(err, en)).toBe(en("common:errors.not_found"));
    expect(problemMessage(err, ko)).toBe(ko("common:errors.not_found"));
    expect(problemMessage(err, ko)).not.toContain("not found");
  });

  it("prefers a surface's own wording over the shared sentence", () => {
    // The gate card says "this project no longer exists or you no longer have
    // access", which is more use than the generic not-found line.
    const prefix = "project_detail:overview.gate_card.errors";
    const scoped = ko(`${prefix}.not_found`);
    expect(scoped).not.toBe(ko("common:errors.not_found"));

    expect(problemMessage(problem(404), ko, { prefix })).toBe(scoped);
  });

  it("falls through to the shared sentence when the surface is silent", () => {
    // The same prefix names no wording for 429, so the shared one answers
    // rather than the surface's unrelated `unknown` copy.
    const prefix = "project_detail:overview.gate_card.errors";
    expect(ko(`${prefix}.rate_limited`, { defaultValue: "" })).toBe("");

    expect(problemMessage(problem(429), ko, { prefix })).toBe(
      ko("common:errors.rate_limited"),
    );
  });

  it("falls back to detail only when the class is unrecognized", () => {
    const err = problem(418, "I am a teapot");
    expect(problemMessage(err, ko)).toBe("I am a teapot");
  });

  it("suppresses the detail fallback when asked", () => {
    const err = problem(418, "I am a teapot");
    expect(problemMessage(err, ko, { allowDetailFallback: false })).toBe(
      ko("common:errors.request_failed"),
    );
  });

  it("never surfaces the axios transport message", () => {
    // status 0 carries "Network Error" as its detail, which is English and
    // tells the user nothing they can act on.
    const err = problem(0, "Network Error");
    const text = problemMessage(err, ko);

    expect(text).toBe(ko("common:errors.network"));
    expect(text).not.toContain("Network Error");
  });

  it("handles an error that is not a Problem at all", () => {
    expect(problemMessage(new Error("boom"), ko)).toBe(
      ko("common:errors.request_failed"),
    );
  });

  it("answers in Korean for every class it names", () => {
    // The point of the helper: no English reaches a Korean session on a path
    // the class table covers.
    for (const status of [0, 401, 403, 404, 409, 429, 500]) {
      const text = problemMessage(problem(status), ko);
      expect(text, `status ${status}`).not.toContain("English");
      expect(text).toMatch(/[가-힣]/);
    }
  });
});
