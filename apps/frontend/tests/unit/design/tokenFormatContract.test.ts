/**
 * W18 — a token's format has to match how the config reads it.
 *
 * `tailwind.config.ts` reads each colour token one of two ways:
 *
 *   hsl(var(--muted))   the token holds space-separated HSL channels
 *   var(--risk-high)    the token holds a complete colour, i.e. a hex
 *
 * The two are not interchangeable. A hex under the first spelling produces
 * `hsl(#f87171)`, which is not a colour, so the browser drops the
 * declaration and the element renders with nothing — the same silent
 * nothing G0-7 spent two days on, arrived at from the other direction.
 *
 * This is exactly what happened while writing the dark theme:
 * `--destructive` was declared as `#f87171` in `.dark` while the config
 * wrapped it in `hsl()`. Every existing gate passed. The contrast tests
 * read the stylesheet and saw a valid colour; the tint compiler test only
 * covers the risk family; nothing compared a declaration against the
 * expression that consumes it. It would have shipped as "destructive
 * buttons have no background in dark mode".
 *
 * So the check reads both sides — which spelling the config uses, and which
 * format the stylesheet wrote — for every token, in `:root` and `.dark`
 * alike. A theme doubles the declarations and therefore doubles the chances
 * of getting this wrong exactly once.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.join(__dirname, "..", "..", "..");
const css = fs.readFileSync(
  path.join(FRONTEND_ROOT, "src", "index.css"),
  "utf8",
);
const config = fs.readFileSync(
  path.join(FRONTEND_ROOT, "tailwind.config.ts"),
  "utf8",
);

type Wrapping = "hsl" | "raw";

/**
 * The `colors` block of the config, by brace balance.
 *
 * Scoped rather than searching the whole file, because `spacing`,
 * `borderRadius`, `boxShadow` and `transitionDuration` also read tokens
 * through `var()` — and `--table-row: 40px` is not a colour that failed to
 * be a hex. The first version of this test reported twelve of those.
 */
function colorsBlock(): string {
  const start = config.indexOf("colors: {");
  if (start < 0) throw new Error("tailwind.config.ts has no `colors` block");
  let depth = 0;
  for (let i = config.indexOf("{", start); i < config.length; i++) {
    if (config[i] === "{") depth++;
    else if (config[i] === "}" && --depth === 0) {
      return config.slice(start, i + 1);
    }
  }
  throw new Error("unbalanced braces in the `colors` block");
}

/**
 * How the config consumes each colour token.
 *
 * Read from the config text rather than listed here, so a token added with
 * the wrong wrapping is caught by the same test instead of quietly falling
 * outside its knowledge.
 */
function wrappings(): Map<string, Wrapping> {
  const config = colorsBlock();
  const out = new Map<string, Wrapping>();
  for (const m of config.matchAll(/hsl\(var\(--([a-z0-9-]+)\)\)/g)) {
    out.set(m[1], "hsl");
  }
  // `var(--x)` not preceded by `hsl(`. The risk family reaches Tailwind
  // through a `color-mix()` (G0-7), which is also a raw consumer.
  for (const m of config.matchAll(/(?<!hsl\()var\(--([a-z0-9-]+)\)/g)) {
    if (!out.has(m[1])) out.set(m[1], "raw");
  }
  for (const m of config.matchAll(/color-mix\([^)]*var\((--[a-z0-9-]+)\)/g)) {
    out.set(m[1].replace(/^--/, ""), "raw");
  }
  // The risk family is wrapped by a helper that takes bare token names.
  for (const m of config.matchAll(/"(--risk-[a-z-]+)"/g)) {
    out.set(m[1].replace(/^--/, ""), "raw");
  }
  return out;
}

const HSL_TRIPLE = /^[\d.]+\s+[\d.]+%\s+[\d.]+%$/;
const HEX = /^#[0-9a-fA-F]{6}$/;

interface Declaration {
  block: ":root" | ".dark";
  name: string;
  value: string;
}

function declarations(): Declaration[] {
  const rootStart = css.indexOf(":root {");
  const darkStart = css.indexOf(".dark {", rootStart);
  const darkEnd = css.indexOf("\n  }", darkStart);

  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "");
  const blocks: Array<[Declaration["block"], string]> = [
    [":root", strip(css.slice(rootStart, darkStart))],
    [".dark", strip(css.slice(darkStart, darkEnd))],
  ];

  const out: Declaration[] = [];
  for (const [block, text] of blocks) {
    for (const m of text.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) {
      out.push({ block, name: m[1], value: m[2].trim() });
    }
  }
  return out;
}

const consumed = wrappings();
const declared = declarations();

describe("token format contract", () => {
  it("finds both sides of the contract", () => {
    // Vacuous-pass guard: if either regex stops matching, every `it.each`
    // below silently covers nothing.
    expect(consumed.size).toBeGreaterThan(20);
    expect(declared.filter((d) => d.block === ":root").length).toBeGreaterThan(
      40,
    );
    expect(declared.filter((d) => d.block === ".dark").length).toBeGreaterThan(
      30,
    );
  });

  const colourDeclarations = declared.filter((d) => consumed.has(d.name));

  it("covers the dark block too", () => {
    // The bug this test exists for was in `.dark` only. A version of this
    // suite that happened to read just `:root` would have passed.
    expect(
      colourDeclarations.some((d) => d.block === ".dark"),
    ).toBe(true);
  });

  it.each(
    colourDeclarations.map((d) => [`${d.block} --${d.name}`, d] as const),
  )("%s is written in the format the config reads", (_label, decl) => {
    const wrapping = consumed.get(decl.name)!;
    const isHsl = HSL_TRIPLE.test(decl.value);
    const isHex = HEX.test(decl.value);

    expect(
      isHsl || isHex,
      `--${decl.name} in ${decl.block} is neither HSL channels nor a hex: ` +
        `"${decl.value}"`,
    ).toBe(true);

    if (wrapping === "hsl") {
      expect(
        isHsl,
        `tailwind.config.ts reads --${decl.name} as hsl(var(--${decl.name})), ` +
          `so it must hold space-separated HSL channels. ${decl.block} has ` +
          `"${decl.value}", which renders as hsl(${decl.value}) — not a ` +
          `colour. The declaration is dropped and anything painted with it ` +
          `gets no colour at all.`,
      ).toBe(true);
    } else {
      expect(
        isHex,
        `tailwind.config.ts reads --${decl.name} directly, so it must hold a ` +
          `complete colour. ${decl.block} has "${decl.value}", which is only ` +
          `channels — the value reaches CSS as three numbers.`,
      ).toBe(true);
    }
  });
});
