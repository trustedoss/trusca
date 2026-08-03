/**
 * G0-7 — the opacity modifier on a risk token must actually emit a rule.
 *
 * The defect this guards against produced no error, no warning, and no
 * visible diff in review. `bg-risk-critical/10` was written at 27 call sites
 * across 13 files, and the design-system doc described that spelling as the
 * way to tint a surface. Tailwind implements the modifier by splitting the
 * colour into channels and re-emitting it with an alpha; the tokens held a
 * bare `var(--risk-critical)`, which it cannot split, so it emitted NOTHING
 * for the slashed class. Every one of those elements rendered transparent
 * for as long as the spelling existed. The only way to see it was to compile
 * the stylesheet and grep, or to read a computed style in a browser.
 *
 * So this test compiles the stylesheet and greps. Asserting the shape of
 * `tailwind.config.ts` instead would be asserting our theory of the fix;
 * running the compiler asserts the fix. It is also why the check is not
 * folded into `tokenConsumers.test.ts` — that one reads source text, and
 * source text is exactly what looked fine here.
 */
import postcss from "postcss";
import tailwind from "tailwindcss";
import { describe, expect, it } from "vitest";

import config from "../../../tailwind.config";

/** The three steps the tint scale allows — see the note in `index.css`. */
const STEPS = ["10", "20", "40"] as const;
const SHADES = ["critical", "high", "medium", "low", "info"] as const;

const CLASSES = SHADES.flatMap((shade) =>
  STEPS.map((step) => `bg-risk-${shade}/${step}`),
).concat(
  SHADES.map((shade) => `border-risk-${shade}/40`),
  // A variant, because the bespoke `.risk-tint-*` classes G0-7 removed could
  // not carry one — `hover:bg-risk-low/20` is a live call site.
  ["hover:bg-risk-low/20"],
);

async function compile(classes: readonly string[]): Promise<string> {
  const result = await postcss([
    tailwind({
      ...config,
      content: [{ raw: classes.map((c) => `<i class="${c}">`).join(""), extension: "html" }],
    }),
  ]).process("@tailwind utilities;", { from: undefined });
  return result.css;
}

describe("risk tint opacity modifier", () => {
  it("emits a rule for every step of every shade", async () => {
    const css = await compile(CLASSES);
    // A class Tailwind dropped leaves no trace, so collect the misses rather
    // than failing on the first — the useful failure message is the whole
    // list, which is how you tell "one shade regressed" from "the mechanism
    // is gone again".
    const missing = CLASSES.filter(
      (c) => !css.includes(`.${c.replace(/[:/]/g, "\\$&")}`),
    );
    expect(missing).toEqual([]);
  });

  it("resolves the emitted colour to the severity token, not a copy of it", async () => {
    const css = await compile(["bg-risk-critical/10"]);
    expect(css).toContain("var(--risk-critical)");
    // `index.css` is the single source of the hue. A config that inlined
    // `#dc2626` here would pass the test above and silently fork the value.
    expect(css).not.toContain("#dc2626");
  });
});
