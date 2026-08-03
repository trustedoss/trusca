/**
 * G0-8 — the design-system doc and the stylesheet must agree.
 *
 * `design-system.md` is named in CLAUDE.md as the single source of truth for
 * the token catalogue, which means contributors read it instead of
 * `index.css`. Nothing checked that the two said the same thing, and they
 * did not:
 *
 *   - G0-7 found `--risk-critical-foreground` and two siblings listed in the
 *     doc as though they had shipped in G0-1. They had never been declared.
 *     Every call site that "used" them was painting an inherited colour.
 *   - This test's first run found `--muted-foreground` documented as
 *     `#71717a` when the stylesheet had moved to `#6c6c75` (a W11 a11y fix
 *     the doc never heard about), `--border`/`--input` off by two channels,
 *     and `--destructive` claiming a hex that `0 72% 51%` does not produce.
 *
 * None of those are visible in review: a token table and a `:root` block are
 * never in the same diff, and both look right in isolation. That is the same
 * shape as CLAUDE.md hardening rule #2 — the same vocabulary in two places
 * needs a parity assertion, or each side stays green while the pair drifts.
 *
 * Three directions are checked, and they are not redundant:
 *
 *   doc -> css    a documented token must exist and hold the documented
 *                 value. Catches vocabulary that was only ever promised.
 *   css -> doc    a token in a family this project owns must be documented.
 *                 Catches colours shipped without an entry, which is how a
 *                 contributor ends up inventing a second one for the same
 *                 job.
 *   EN <-> KO     the mirrors must carry identical token tables. A value
 *                 corrected in one language and not the other is drift that
 *                 reads as authoritative in both.
 *
 * Prose is deliberately out of scope — only the tables are parsed. Holding
 * translated sentences to equality would make the gate argue about wording,
 * and a gate that argues about wording gets switched off.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.join(__dirname, "..", "..", "..", "..", "..");
const CSS_PATH = path.join(
  REPO_ROOT,
  "apps/frontend/src/index.css",
);
const DOC_EN = path.join(
  REPO_ROOT,
  "docs-site/docs/reference/design-system.md",
);
const DOC_KO = path.join(
  REPO_ROOT,
  "docs-site/i18n/ko/docusaurus-plugin-content-docs/current/reference/design-system.md",
);

/**
 * Families this project owns and therefore must document. The shadcn base
 * set is upstream's contract and the layout/motion tokens are dimensions
 * rather than colours; both are checked in the doc -> css direction (if the
 * doc names one, it must be right) but not required to appear.
 *
 * Same boundary `tokenConsumers.test.ts` draws, for the same reason: these
 * are the vocabularies a contributor picks from when naming a new surface.
 */
const OWNED = [/^risk-/, /^status-/, /^brand(-|$)/, /^topbar(-|$)/];

/** `240 4% 44%` and `0 72% 50.6%` alike — HSL as shadcn stores it. */
const HSL_TRIPLE = /^([\d.]+)\s+([\d.]+)%\s+([\d.]+)%$/;

function hslToHex(h: number, s: number, l: number): string {
  const sat = s / 100;
  const lig = l / 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = sat * Math.min(lig, 1 - lig);
  const f = (n: number) =>
    lig - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  const channel = (x: number) =>
    Math.round(255 * x)
      .toString(16)
      .padStart(2, "0");
  return `#${channel(f(0))}${channel(f(8))}${channel(f(4))}`;
}

interface Declaration {
  /** As written in the stylesheet — `#dc2626` or `0 72% 50.6%`. */
  raw: string;
  /** Resolved to hex, or null for non-colour values (px, ms, easing). */
  hex: string | null;
  /** The `/* #xxxxxx *\/` note beside an HSL triple, if there is one. */
  commentHex: string | null;
}

/** Which stylesheet block, and which half of the doc, a token belongs to. */
type Theme = "light" | "dark";

/** Declarations from one block of `index.css`. */
function cssTokens(theme: Theme = "light"): Map<string, Declaration> {
  const css = fs.readFileSync(CSS_PATH, "utf8");
  const rootStart = css.indexOf(":root {");
  const darkStart = css.indexOf(".dark {", rootStart);
  const block =
    theme === "light"
      ? css.slice(rootStart, darkStart === -1 ? undefined : darkStart)
      : css.slice(darkStart, css.indexOf("\n  }", darkStart));

  const out = new Map<string, Declaration>();
  for (const line of block.split("\n")) {
    const m = line.match(
      /^\s*--([a-z0-9-]+):\s*([^;]+);(?:\s*\/\*\s*(#[0-9a-fA-F]{6}))?/,
    );
    if (!m) continue;
    const [, name, rawValue, comment] = m;
    const raw = rawValue.trim();
    let hex: string | null = null;
    if (/^#[0-9a-fA-F]{6}$/.test(raw)) hex = raw.toLowerCase();
    else {
      const hsl = raw.match(HSL_TRIPLE);
      if (hsl) hex = hslToHex(+hsl[1], +hsl[2], +hsl[3]);
    }
    out.set(name, { raw, hex, commentHex: comment?.toLowerCase() ?? null });
  }
  return out;
}

/**
 * Token rows in a markdown table: `| `--token` | `#hex` | …`.
 *
 * The status family is spelled differently — one row per state, one column
 * per suffix — so it gets its own pass below rather than a looser regex
 * here. A looser regex would also swallow the contrast tables, whose first
 * column is an expression (`--muted-foreground` on `--card`) and not a
 * declaration.
 */
function docTokens(file: string, theme: Theme = "light"): Map<string, string> {
  const out = new Map<string, string>();
  for (const line of docHalf(file, theme).split("\n")) {
    const m = line.match(/^\|\s*`--([a-z0-9-]+)`\s*\|\s*`(#[0-9a-fA-F]{6})`/);
    if (m) out.set(m[1], m[2].toLowerCase());
  }
  return out;
}

/**
 * The doc, split at the dark-theme heading (W18).
 *
 * Both halves list the same token names with different values, so a parser
 * reading the whole file would have each row overwrite the other and then
 * compare light values against the dark block — reporting drift on every
 * token while both sides were correct. The split is what makes the second
 * theme checkable at all.
 *
 * The heading is matched loosely enough to survive translation: the Korean
 * mirror keeps the English token names but not the English prose.
 */
const DARK_HEADING = /^## .*\bW18\b/m;

function docHalf(file: string, theme: Theme): string {
  const text = fs.readFileSync(file, "utf8");
  const match = DARK_HEADING.exec(text);
  if (!match) {
    throw new Error(
      `${path.basename(file)} has no dark-theme section heading — the W18 ` +
        `tables cannot be told apart from the light ones.`,
    );
  }
  const at = match.index;
  if (theme === "light") return text.slice(0, at);
  // The dark section runs to the next top-level heading.
  const rest = text.slice(at);
  const nextHeading = rest.slice(1).search(/^## /m);
  return nextHeading === -1 ? rest : rest.slice(0, nextHeading + 1);
}

/**
 * The status table: `| `success` | `#059669` | `#ecfdf5` | … |`, columns in
 * suffix order, `—` where a solid is deliberately not declared.
 *
 * Anchored on its header row rather than on the state names, because the
 * severity-accessibility section further down also opens rows with
 * `| `info` |` and carries a hex of its own. Matching on the names alone
 * mapped that row's `#52525b` onto `--status-info-subtle` — a parity gate
 * reporting a drift that existed only in its own parser.
 */
const STATUS_HEADER = /^\|\s*Status\s*\|\s*Solid\s*\|\s*Subtle\s*\|\s*Border\s*\|\s*Foreground\s*\|/;
const STATUS_SUFFIXES = ["", "-subtle", "-border", "-foreground"] as const;

function docStatusTokens(file: string, theme: Theme = "light"): Map<string, string> {
  const out = new Map<string, string>();
  const lines = docHalf(file, theme).split("\n");
  const header = lines.findIndex((l) => STATUS_HEADER.test(l));
  if (header === -1) return out;

  // Header, separator, then rows until the table ends.
  for (const line of lines.slice(header + 2)) {
    if (!line.startsWith("|")) break;
    const cells = line.split("|").slice(1, -1).map((c) => c.trim());
    const state = cells[0]?.match(/^`([a-z]+)`$/)?.[1];
    if (!state) continue;
    STATUS_SUFFIXES.forEach((suffix, i) => {
      const hex = cells[i + 1]?.match(/^`(#[0-9a-fA-F]{6})`$/);
      if (hex) out.set(`status-${state}${suffix}`, hex[1].toLowerCase());
    });
  }
  return out;
}

function allDocTokens(file: string, theme: Theme = "light"): Map<string, string> {
  return new Map([...docTokens(file, theme), ...docStatusTokens(file, theme)]);
}

describe.each(["light", "dark"] as const)(
  "design-system doc / stylesheet parity (%s)",
  (theme) => {
    const css = cssTokens(theme);
    const en = allDocTokens(DOC_EN, theme);
    const ko = allDocTokens(DOC_KO, theme);

    it("parses both sides", () => {
      // Vacuous-pass guard, per theme: the dark half is a newer and smaller
      // section, so a heading rename would empty it without emptying light.
      expect(css.size).toBeGreaterThan(theme === "light" ? 50 : 40);
      expect(en.size).toBeGreaterThan(theme === "light" ? 30 : 30);
      expect(ko.size).toBeGreaterThan(theme === "light" ? 30 : 30);
    });

    it.each([...en.keys()].sort())(
      "--%s is documented and declared with the same value",
      (name) => {
        const declared = css.get(name);
        expect(
          declared,
          `--${name} appears in the ${theme} tables of design-system.md but ` +
            `is not declared in that block of index.css.`,
        ).toBeDefined();

        expect(
          declared!.hex,
          `--${name} (${theme}) is documented with a hex but its declaration ` +
            `(${declared!.raw}) does not resolve to a colour.`,
        ).not.toBeNull();

        expect(
          declared!.hex,
          `--${name} (${theme}): the doc says ${en.get(name)}, index.css ` +
            `renders ${declared!.hex} (from \`${declared!.raw}\`).`,
        ).toBe(en.get(name));
      },
    );

    it.each(
      [...css.keys()]
        .filter((name) => OWNED.some((rx) => rx.test(name)))
        .sort(),
    )("--%s is in an owned family, so it must be documented", (name) => {
      expect(
        en.has(name),
        `--${name} is declared in the ${theme} block but no ${theme} table ` +
          `in design-system.md lists it.`,
      ).toBe(true);
    });

    it("the Korean mirror carries the same token table", () => {
      expect(Object.fromEntries([...ko].sort())).toEqual(
        Object.fromEntries([...en].sort()),
      );
    });
  },
);

describe("design-system doc / stylesheet parity", () => {
  const css = cssTokens();
  const en = allDocTokens(DOC_EN);
  const ko = allDocTokens(DOC_KO);

  it("reads a dark section out of both mirrors", () => {
    // The per-theme suites above would each pass vacuously if the split
    // returned an empty dark half — `en.size > 30` is satisfied by the light
    // tables alone if the heading match ever picks the wrong offset. These
    // two tokens exist only in the dark block.
    expect(allDocTokens(DOC_EN, "dark").has("overlay")).toBe(true);
    expect(allDocTokens(DOC_KO, "dark").has("overlay")).toBe(true);
    expect(allDocTokens(DOC_EN, "dark").get("topbar")).toBe("#17213a");
    expect(allDocTokens(DOC_KO, "dark").get("topbar")).toBe("#17213a");
    // And the light half must NOT have picked up dark values.
    expect(en.get("topbar")).toBe("#18181b");
    expect(ko.get("topbar")).toBe("#18181b");
  });

  it.each([...cssTokens("dark")].filter(([, v]) => v.commentHex !== null))(
    "--%s's dark hex comment matches the value it annotates",
    (_name, decl) => {
      expect(
        decl.hex,
        `the comment says ${decl.commentHex}, but \`${decl.raw}\` renders ` +
          `${decl.hex}.`,
      ).toBe(decl.commentHex);
    },
  );

  it.each([...css].filter(([, v]) => v.commentHex !== null))(
    "--%s's hex comment matches the value it annotates",
    (_name, decl) => {
      expect(
        decl.hex,
        `the comment says ${decl.commentHex}, but \`${decl.raw}\` renders ` +
          `${decl.hex}. The comment is what a reader trusts when scanning ` +
          `the block, so a stale one is worse than none.`,
      ).toBe(decl.commentHex);
    },
  );
});
