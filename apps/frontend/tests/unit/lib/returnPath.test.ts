/**
 * safeReturnPath: the open-redirect guard on the sign-in hand-off (A5).
 *
 * The interesting cases are the ones a browser treats as another origin even
 * though they start with a slash, because "it starts with / so it is
 * internal" is the check people write and it is not true.
 */
import { describe, expect, it } from "vitest";

import { DEFAULT_RETURN_PATH, safeReturnPath } from "@/lib/returnPath";

describe("safeReturnPath", () => {
  it("keeps an in-app path, with its query and hash", () => {
    expect(safeReturnPath("/projects/42?tab=components#row-7")).toBe(
      "/projects/42?tab=components#row-7",
    );
  });

  it.each([
    ["protocol-relative", "//evil.example/steal"],
    ["backslash authority", "/\\evil.example/steal"],
    ["absolute https", "https://evil.example/steal"],
    ["absolute http", "http://evil.example"],
    ["scheme with no slash", "javascript:alert(1)"],
    ["data url", "data:text/html,<script>alert(1)</script>"],
    ["bare host", "evil.example/steal"],
    ["empty", ""],
    ["whitespace", "   "],
  ])("refuses a %s target", (_label, candidate) => {
    expect(safeReturnPath(candidate)).toBe(DEFAULT_RETURN_PATH);
  });

  it("refuses a path carrying a control character", () => {
    // A newline here can split a line in a log, or a header in whatever
    // writes one downstream.
    expect(safeReturnPath("/projects\n/x")).toBe(DEFAULT_RETURN_PATH);
    expect(safeReturnPath("/projects\r\nSet-Cookie: a=b")).toBe(
      DEFAULT_RETURN_PATH,
    );
    // Written as escapes: the literal was a NUL, invisible in an editor,
    // and an edit could soften the assertion into "a plain space is
    // rejected", which is not true (the candidate is trimmed first).
    expect(safeReturnPath("/projects\u0000")).toBe(DEFAULT_RETURN_PATH);
    expect(safeReturnPath("/projects\u007fx")).toBe(DEFAULT_RETURN_PATH);
  });

  it("refuses anything that is not a string", () => {
    for (const value of [null, undefined, 42, {}, ["/projects"], true]) {
      expect(safeReturnPath(value)).toBe(DEFAULT_RETURN_PATH);
    }
  });

  it("refuses the auth screens, which are not destinations", () => {
    // Returning to /login after signing in would bounce the user straight
    // back out of the app they just entered.
    for (const path of [
      "/login",
      "/register",
      "/forgot-password",
      "/reset-password",
    ]) {
      expect(safeReturnPath(path)).toBe(DEFAULT_RETURN_PATH);
      expect(safeReturnPath(`${path}?next=/x`)).toBe(DEFAULT_RETURN_PATH);
    }
  });

  it("allows a path that merely starts with an auth screen's name", () => {
    // `/registered-components` is not `/register`, and a prefix match would
    // have quietly sent that screen to the dashboard.
    expect(safeReturnPath("/registered-components")).toBe(
      "/registered-components",
    );
  });

  it("refuses an absurdly long path", () => {
    expect(safeReturnPath(`/${"a".repeat(5000)}`)).toBe(DEFAULT_RETURN_PATH);
  });

  it("trims before deciding, so padding cannot smuggle a host past it", () => {
    expect(safeReturnPath("  //evil.example  ")).toBe(DEFAULT_RETURN_PATH);
    expect(safeReturnPath("  /projects  ")).toBe("/projects");
  });
});
