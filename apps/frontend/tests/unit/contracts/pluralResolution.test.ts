/**
 * Plural resolution contract.
 *
 * `npm run i18n:check` enforces the *shape* of plural keys (no `_plural`
 * suffix, every `_other` has a bare fallback). That is a static check on the
 * JSON. This test closes the other half: that i18next actually resolves those
 * keys at runtime, in both locales.
 *
 * The defect this guards against shipped for months. The build gate's reason
 * for known-malicious packages was written as `malicious_plural` — i18next v3
 * syntax that v4 never looks up — so a build blocked on five malicious
 * packages told the user about one. A key can be present, translated, mirrored
 * across locales, and still dead.
 *
 * Korean has a single CLDR plural category, so KO resolves `_other` for every
 * count including 1. English resolves the bare key for 1 and `_other` above it.
 */
import { describe, expect, it } from "vitest";

import i18n from "@/lib/i18n";

const PLURAL_CATEGORIES = ["zero", "one", "two", "few", "many", "other"];

type Bundle = Record<string, unknown>;

/** Every leaf key path in a resource bundle. */
function leafKeys(obj: unknown, prefix = ""): string[] {
  if (obj === null || typeof obj !== "object" || Array.isArray(obj)) {
    return prefix ? [prefix] : [];
  }
  return Object.entries(obj as Bundle).flatMap(([k, v]) =>
    leafKeys(v, prefix ? `${prefix}.${k}` : k),
  );
}

/** Read a dot-separated key path out of a resource bundle. */
function lookup(bundle: Bundle, key: string): unknown {
  return key
    .split(".")
    .reduce<unknown>(
      (acc, seg) =>
        acc && typeof acc === "object"
          ? (acc as Bundle)[seg]
          : undefined,
      bundle,
    );
}

/**
 * Reduce copy to its shape so a resolved string can be compared against the
 * raw catalogue entry: the count becomes `#`, and any other placeholder is
 * dropped because these assertions pass no value for it.
 */
function shape(text: string): string {
  return text
    .replace(/\{\{\s*count\s*\}\}/g, "#")
    .replace(/\{\{[^}]*\}\}/g, "")
    .replace(/\d+/g, "#");
}

/** Base keys that ship a plural variant, per namespace. */
function pluralBases(bundle: Bundle): string[] {
  const keys = leafKeys(bundle);
  const bases = new Set<string>();
  for (const key of keys) {
    const leaf = key.slice(key.lastIndexOf(".") + 1);
    const category = PLURAL_CATEGORIES.find((c) => leaf.endsWith(`_${c}`));
    if (category) bases.add(key.slice(0, key.length - `_${category}`.length));
  }
  return [...bases].sort();
}

const NAMESPACES = (i18n.options.ns as string[]) ?? [];

describe("plural resolution", () => {
  it("has namespaces loaded (guards against an empty sweep passing vacuously)", () => {
    expect(NAMESPACES.length).toBeGreaterThan(0);
  });

  const cases = NAMESPACES.flatMap((ns) => {
    const bundle = (i18n.getResourceBundle("en", ns) ?? {}) as Bundle;
    return pluralBases(bundle).map((key) => ({ ns, key }));
  });

  it("finds the plural keys the locales ship", () => {
    // If this drops to zero, the sweep below is testing nothing. It is a
    // deliberate tripwire, not a count worth keeping current: raise it only
    // when a plural key is added, never lower it to make a failure go away.
    expect(cases.length).toBeGreaterThanOrEqual(4);
  });

  it.each(cases)("EN resolves $ns:$key for one and many", ({ ns, key }) => {
    const t = i18n.getFixedT("en", ns);
    const one = t(key, { count: 1 });
    const many = t(key, { count: 5 });

    // A missing key resolves to the key itself.
    expect(one).not.toBe(key);
    expect(many).not.toBe(key);

    // The whole point of a plural variant is that the wording differs beyond
    // the number. Compare with the count stripped out.
    expect(shape(many)).not.toBe(shape(one));

    // And the many-form is the catalogue's `_other`, not some other fallback
    // that happens to differ.
    const bundle = i18n.getResourceBundle("en", ns) as Bundle;
    const other = lookup(bundle, `${key}_other`);
    expect(typeof other).toBe("string");
    expect(shape(many)).toBe(shape(other as string));
  });

  it.each(cases)("KO resolves $ns:$key through _other", ({ ns, key }) => {
    const t = i18n.getFixedT("ko", ns);
    const one = t(key, { count: 1 });
    const many = t(key, { count: 5 });

    expect(one).not.toBe(key);
    expect(many).not.toBe(key);

    // Korean has a single CLDR category, so every count resolves `_other` and
    // the number carries the plurality. Asserting the variant exists matters:
    // KO's bare string is usually identical, so a deleted `_other` would fall
    // back silently and a wording-only assertion would not notice.
    const bundle = i18n.getResourceBundle("ko", ns) as Bundle;
    const other = lookup(bundle, `${key}_other`);
    expect(typeof other).toBe("string");
    expect(shape(many)).toBe(shape(other as string));
    expect(shape(one)).toBe(shape(many));
    expect(many).toContain("5");
  });
});
