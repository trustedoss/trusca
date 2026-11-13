/**
 * `readToggleOn` replaces a `data-state` read that returned `null` on every
 * call (see the helper). The cases that matter are the ones the old read got
 * wrong: an attribute that is missing must fail, not pass as "off".
 */
import { describe, expect, it } from "vitest";

import { readToggleOn, type AttributeReader } from "../../_harness/ariaState";

function control(attrs: Record<string, string>): AttributeReader {
  return { getAttribute: async (name) => attrs[name] ?? null };
}

describe("readToggleOn", () => {
  it.each([
    [{ "aria-checked": "true" }, true],
    [{ "aria-checked": "false" }, false],
    [{ "aria-pressed": "true" }, true],
    [{ "aria-pressed": "false" }, false],
    // A switch built on a native checkbox may still carry a library's own
    // attribute next to ARIA; only ARIA decides.
    [{ "aria-checked": "false", "data-state": "checked" }, false],
  ])("reads %o as %s", async (attrs, expected) => {
    await expect(readToggleOn(control(attrs))).resolves.toBe(expected);
  });

  it("throws when the control exposes no ARIA state", async () => {
    await expect(readToggleOn(control({ "data-state": "checked" }))).rejects.toThrow(
      /neither aria-checked nor aria-pressed/,
    );
  });

  it("throws on a value that is neither true nor false", async () => {
    await expect(readToggleOn(control({ "aria-checked": "mixed" }))).rejects.toThrow();
  });
});
