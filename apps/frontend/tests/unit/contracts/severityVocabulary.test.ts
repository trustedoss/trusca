/**
 * Severity vocabulary contract.
 *
 * The severity scale is written out in eight places across the locale files.
 * Nothing tied them together, so they drifted: Korean said 치명 in five of
 * them, 심각 in the inventory, and left the portfolio legend in English. A
 * user moving between two screens saw two different names for the same tier,
 * which reads as two different things.
 *
 * Rather than list the known copies (a list goes stale the moment someone adds
 * a ninth), this sweeps the catalogues for any object that names the whole
 * scale and holds it to the canonical wording in `common:risk`. A new screen
 * that spells the scale out is caught on the day it lands.
 *
 * Deliberate exceptions are listed below with the reason, and each one is
 * asserted to still exist — an allowlist that silently stops matching is an
 * allowlist that hides the next defect.
 */
import { describe, expect, it } from "vitest";

import i18n from "@/lib/i18n";

/** The tiers that make something a severity scale rather than an enum. */
const SCALE = ["critical", "high", "medium", "low"] as const;

const CANONICAL_NS = "common";
const CANONICAL_PATH = "risk";

/**
 * Scales that legitimately differ from the canonical wording. Keyed by
 * `<namespace>.<path>`.
 */
const EXEMPT: Record<string, string> = {
  "project_detail.risk":
    "Risk level, not CVE severity. Reads '<tier> risk' as a whole phrase.",
  "project_detail.releases.severity_abbr":
    "Single-letter abbreviations for a dense release table.",
  "projects.severity.abbrev":
    "Single-letter abbreviations for the project list summary bar.",
};

type Bundle = Record<string, unknown>;

/** Every object in a bundle that names the full scale, with its dotted path. */
function findScales(node: unknown, path = ""): { path: string; values: string[] }[] {
  if (!node || typeof node !== "object" || Array.isArray(node)) return [];
  const obj = node as Bundle;
  const found: { path: string; values: string[] }[] = [];
  if (SCALE.every((tier) => typeof obj[tier] === "string")) {
    found.push({ path, values: SCALE.map((tier) => obj[tier] as string) });
  }
  for (const [key, value] of Object.entries(obj)) {
    found.push(...findScales(value, path ? `${path}.${key}` : key));
  }
  return found;
}

const LOCALES = ["en", "ko"] as const;
const NAMESPACES = (i18n.options.ns as string[]) ?? [];

function scalesFor(locale: string) {
  return NAMESPACES.flatMap((ns) =>
    findScales(i18n.getResourceBundle(locale, ns) ?? {}).map((scale) => ({
      ...scale,
      id: `${ns}.${scale.path}`,
    })),
  );
}

describe("severity vocabulary", () => {
  it.each(LOCALES)("%s spells the scale the same way everywhere", (locale) => {
    const canonical = findScales(
      i18n.getResourceBundle(locale, CANONICAL_NS) ?? {},
    ).find((scale) => scale.path === CANONICAL_PATH);
    expect(canonical, `${locale}:${CANONICAL_NS}.${CANONICAL_PATH} is missing`)
      .toBeDefined();

    const offenders = scalesFor(locale)
      .filter((scale) => !(scale.id in EXEMPT))
      .filter(
        (scale) => scale.values.join("|") !== canonical!.values.join("|"),
      )
      .map((scale) => `${scale.id} = ${scale.values.join(" / ")}`);

    expect(
      offenders,
      `these scales disagree with ${CANONICAL_NS}.${CANONICAL_PATH} ` +
        `(${canonical!.values.join(" / ")}). Match the canonical wording, or ` +
        `add an entry to EXEMPT with the reason.`,
    ).toEqual([]);
  });

  it.each(LOCALES)("%s still contains every exempted scale", (locale) => {
    const present = new Set(scalesFor(locale).map((scale) => scale.id));
    for (const id of Object.keys(EXEMPT)) {
      expect(present.has(id), `EXEMPT names ${id}, which no longer exists`).toBe(
        true,
      );
    }
  });

  it.each(LOCALES)("%s sweeps every scale the catalogue ships", (locale) => {
    // Guards against a refactor that empties the sweep and leaves it passing.
    // Pinned to the exact count, not a floor: `findScales` only recognizes an
    // object that names all four tiers, so deleting one key would drop a whole
    // scale out of the sweep, and a floor with slack would not notice.
    expect(scalesFor(locale).map((scale) => scale.id).sort()).toEqual([
      "common.risk",
      "dashboard.portfolio.bucket",
      "inventory.severity",
      "project_detail.releases.severity_abbr",
      "project_detail.risk",
      "project_detail.severity",
      "project_detail.vulnerabilities.severity",
      "projects.severity.abbrev",
    ]);
  });

  it("labels the severity axis itself consistently", () => {
    // A plain `severity` label with no interpolation is the axis name. It has
    // one right answer per locale; anything else is a second vocabulary.
    for (const locale of LOCALES) {
      const expected = locale === "ko" ? "심각도" : "Severity";
      const wrong: string[] = [];
      for (const ns of NAMESPACES) {
        const visit = (node: unknown, path: string) => {
          if (!node || typeof node !== "object" || Array.isArray(node)) return;
          for (const [key, value] of Object.entries(node as Bundle)) {
            const here = path ? `${path}.${key}` : key;
            if (
              key === "severity" &&
              typeof value === "string" &&
              !value.includes("{{") &&
              value !== expected
            ) {
              wrong.push(`${locale}:${ns}.${here} = ${value}`);
            }
            visit(value, here);
          }
        };
        visit(i18n.getResourceBundle(locale, ns) ?? {}, "");
      }
      expect(wrong).toEqual([]);
    }
  });
});
