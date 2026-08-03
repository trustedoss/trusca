/**
 * G0-1 — status / risk-tint token contrast gate.
 *
 * These tokens exist because call sites were reaching for raw Tailwind
 * palette classes (`text-yellow-800` on a risk tint, `border-amber-300
 * bg-amber-50 text-amber-900` for a warning callout) — each site picking
 * its own shade, none of them contrast-checked. The token lint
 * (`scripts/token-lint.mjs`) blocks the raw classes; this test blocks the
 * other half of the problem: a token whose value quietly stops meeting
 * WCAG when someone "adjusts the tone".
 *
 * Values are read from `src/index.css` itself, not duplicated here — a
 * hand-copied table would drift from the stylesheet and assert nothing.
 *
 * Thresholds:
 *   - `--status-X-foreground` on `--status-X-subtle`  ≥ 4.5:1 (WCAG AA text)
 *   - `--risk-X-foreground` on `bg-risk-X/20` over --background ≥ 4.5:1
 *   - `--status-X` solid on `--background`            ≥ 3:1 (WCAG 1.4.11,
 *     non-text UI: dots, bars)
 *   - `--status-X-border` is deliberately NOT gated — the tint plus the
 *     foreground already carry the meaning, so the border is decorative.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CSS_PATH = path.join(__dirname, "..", "..", "..", "src", "index.css");

// --- WCAG helpers (sRGB relative luminance) ------------------------------

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "").trim();
  return [
    parseInt(h.slice(0, 2), 16) / 255,
    parseInt(h.slice(2, 4), 16) / 255,
    parseInt(h.slice(4, 6), 16) / 255,
  ];
}

function relLuminance([r, g, b]: [number, number, number]): number {
  const f = (c: number) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [
    relLuminance(hexToRgb(a)),
    relLuminance(hexToRgb(b)),
  ].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Composite `fg` at `alpha` over `bg` — what `bg-risk-X/15` renders as. */
function composite(fg: string, alpha: number, bg: string): string {
  const F = hexToRgb(fg);
  const B = hexToRgb(bg);
  return `#${F.map((v, i) =>
    Math.round((v * alpha + B[i] * (1 - alpha)) * 255)
      .toString(16)
      .padStart(2, "0"),
  ).join("")}`;
}

// --- Token extraction ----------------------------------------------------

const css = fs.readFileSync(CSS_PATH, "utf8");

function token(name: string): string {
  // Only the `:root` block matters here; the `.dark` block (populated in
  // W18) declares the same names and must not shadow the light values.
  // Anchor on the selectors, not bare substrings — the file header comment
  // mentions `.dark` well before `:root` opens.
  const rootStart = css.indexOf(":root {");
  const darkStart = css.indexOf(".dark {", rootStart);
  if (rootStart < 0 || darkStart < 0) {
    throw new Error("index.css no longer has a :root { … } / .dark { … } pair");
  }
  const rootBlock = withoutComments(css.slice(rootStart, darkStart));
  const m = new RegExp(`--${name}:\\s*([^;]+);`).exec(rootBlock);
  if (!m) {
    throw new Error(`token --${name} is not declared in the :root block`);
  }
  return resolve(name, m[1].trim());
}

/**
 * The same extraction for the `.dark` block (W18), and for HSL as well as
 * hex.
 *
 * The light half of this file hardcodes `#fafafa` and friends because the
 * neutral tokens are HSL triplets and it had one or two of them to deal
 * with. Dark doubles the count and inverts every one, so it reads them
 * properly instead — a hardcoded hex beside an HSL declaration is the exact
 * drift G0-8 found four instances of in the design-system doc.
 */
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

const darkBlockStart = css.indexOf(".dark {");
const darkBlock = css.slice(darkBlockStart, css.indexOf("\n  }", darkBlockStart));

/**
 * The block with its comments removed.
 *
 * Both blocks explain themselves by quoting declarations — "`--topbar:
 * #18181b` was ink against a white app" is prose, and the first version of
 * this reader took it for the value of `--topbar`. The light reader has the
 * same exposure and gets away with it only because its comments happen to
 * sit after the declarations they discuss.
 */
function withoutComments(block: string): string {
  return block.replace(/\/\*[\s\S]*?\*\//g, "");
}

const darkDeclarations = withoutComments(darkBlock);

/** A declared value as hex, whether the stylesheet wrote hex or HSL. */
function resolve(name: string, raw: string): string {
  if (/^#[0-9a-fA-F]{6}$/.test(raw)) return raw.toLowerCase();
  const hsl = raw.match(/^([\d.]+)\s+([\d.]+)%\s+([\d.]+)%$/);
  if (!hsl) {
    throw new Error(`--${name} is neither a hex nor an HSL triplet: ${raw}`);
  }
  return hslToHex(+hsl[1], +hsl[2], +hsl[3]);
}

/** A `.dark` token, resolved to hex whether it is written as hex or HSL. */
function dark(name: string): string {
  const m = new RegExp(`--${name}:\\s*([^;]+);`).exec(darkDeclarations);
  if (!m) {
    throw new Error(`token --${name} is not declared in the .dark block`);
  }
  return resolve(name, m[1].trim());
}

const STATUSES = ["success", "warning", "danger", "info"] as const;
/**
 * Statuses that declare a solid fill. `success` has the finished-scan
 * progress bar; `danger` has the dashboard trend's "new findings" bars.
 * The other two are callout surfaces only, and `tokenConsumers.test.ts`
 * refuses to let an unused solid sit in the stylesheet collecting an
 * untested contrast promise.
 */
const SOLID_STATUSES = ["success", "danger"] as const;
/**
 * Every risk shade, because after G0-7 every one of them has a call site.
 * The list was `["medium", "low"]` while the other three were spelled as raw
 * palette classes at their call sites.
 */
const RISKS = ["critical", "high", "medium", "low", "info"] as const;

/**
 * The worst case a risk foreground has to survive.
 *
 * `/20` is the densest step in the tint scale (`/10` surface, `/20` the same
 * surface emphasised, `/40` border) — a lighter tint only raises contrast for
 * dark text. `--background` rather than `--card` because it is the darker of
 * the two grounds a tint composites over, and dark-on-tint contrast falls as
 * the ground darkens.
 *
 * This pair was `0.15` over `#ffffff` before G0-7, and it asserted nothing:
 * Tailwind emitted no rule for a slashed risk class, so the surface being
 * measured here had never once been rendered. It renders now.
 */
const RISK_TINT_ALPHA = 0.2;
const RISK_TINT_GROUND = "#fafafa";

describe("status token contrast", () => {
  it.each(STATUSES)(
    "--status-%s-foreground clears AA on its subtle surface",
    (name) => {
      const ratio = contrast(
        token(`status-${name}-foreground`),
        token(`status-${name}-subtle`),
      );
      expect(ratio).toBeGreaterThanOrEqual(4.5);
    },
  );

  it.each(SOLID_STATUSES)(
    "--status-%s solid clears the 3:1 non-text floor on --background",
    (name) => {
      // --background is an HSL triplet (0 0% 98%) → #fafafa.
      const ratio = contrast(token(`status-${name}`), "#fafafa");
      expect(ratio).toBeGreaterThanOrEqual(3);
    },
  );
});

describe("brand accent and global bar (W14)", () => {
  it.each([
    // The surfaces the accent is actually painted on. An earlier version of
    // this test measured against white, which the indicator never sits on —
    // it would have kept passing while a retune of --brand-subtle or --muted
    // pushed the real contrast under the floor.
    ["--brand-subtle (active nav indicator)", "brand-subtle"],
    ["--muted (progress track)", null],
  ] as const)("--brand clears 3:1 on %s", (_label, surfaceToken) => {
    const surface = surfaceToken ? token(surfaceToken) : "#f4f4f5";
    expect(contrast(token("brand"), surface)).toBeGreaterThanOrEqual(3);
  });

  it("keeps body text readable on the active nav row", () => {
    // The row tints its ground; the label must not need the accent to be
    // legible, which is also why the label stays ink rather than teal.
    expect(contrast("#18181b", token("brand-subtle"))).toBeGreaterThanOrEqual(
      4.5,
    );
  });

  it.each([
    ["--topbar-foreground", "topbar-foreground"],
    ["--topbar-muted-foreground", "topbar-muted-foreground"],
    ["--brand-on-ink", "brand-on-ink"],
  ] as const)("%s is readable on the ink bar", (_label, name) => {
    expect(contrast(token(name), token("topbar"))).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps the two teals distinct in role", () => {
    // Two tokens because one hue cannot do both jobs: the interface teal is
    // too dark to read on ink, the logo teal too light for white. Asserting
    // the pairing each is FOR, rather than asserting the logo teal fails on
    // white — a test that breaks when someone improves a value is a trap.
    expect(contrast(token("brand-on-ink"), token("topbar"))).toBeGreaterThan(
      contrast(token("brand"), token("topbar")),
    );
    expect(contrast(token("brand"), "#ffffff")).toBeGreaterThan(
      contrast(token("brand-on-ink"), "#ffffff"),
    );
  });
});

describe("neutral text contrast", () => {
  // The comment on --muted-foreground used to assert "passes AA on --muted"
  // while the value measured 4.40:1. A claim in a comment is not a check;
  // this is.
  const SURFACES = [
    ["--background", "#fafafa"],
    ["--card", "#ffffff"],
    ["--muted", "#f4f4f5"],
  ] as const;

  it.each(SURFACES)("--muted-foreground clears AA on %s", (_name, surface) => {
    // HSL triplet in index.css → the hex it resolves to.
    expect(contrast("#6c6c75", surface)).toBeGreaterThanOrEqual(4.5);
  });

  it("still declares the lightness those numbers were computed from", () => {
    // Guards the pair above: if someone retunes the triplet, the hardcoded
    // hex would keep passing while the app renders something else.
    const rootBlock = css.slice(
      css.indexOf(":root {"),
      css.indexOf(".dark {", css.indexOf(":root {")),
    );
    expect(rootBlock).toContain("--muted-foreground: 240 4% 44%");
  });
});

describe("risk tint foreground contrast", () => {
  it.each(RISKS)(
    "--risk-%s-foreground clears AA on its own densest tint",
    (name) => {
      const tint = composite(
        token(`risk-${name}`),
        RISK_TINT_ALPHA,
        RISK_TINT_GROUND,
      );
      const ratio = contrast(token(`risk-${name}-foreground`), tint);
      expect(ratio).toBeGreaterThanOrEqual(4.5);
    },
  );

  it.each(RISKS)(
    "--risk-%s itself is NOT readable on its tint — the reason the foregrounds exist",
    (name) => {
      // The pairing call sites reached for before G0-7, and the one a future
      // edit is most likely to reach for again: the severity hex as text on
      // its own tint. Asserting it fails keeps the comment in `index.css`
      // honest, and turns "why not just use text-risk-high?" into a red test
      // rather than an argument.
      const tint = composite(
        token(`risk-${name}`),
        RISK_TINT_ALPHA,
        RISK_TINT_GROUND,
      );
      expect(contrast(token(`risk-${name}`), tint)).toBeLessThan(4.5);
    },
  );

  it("keeps the severity fills themselves unchanged (brand rule)", () => {
    // CLAUDE.md forbids changing these hexes — legibility is solved with the
    // `-foreground` shades above, never by retuning the severity color.
    expect({
      critical: token("risk-critical"),
      high: token("risk-high"),
      medium: token("risk-medium"),
      low: token("risk-low"),
      info: token("risk-info"),
    }).toEqual({
      critical: "#dc2626",
      high: "#ea580c",
      medium: "#ca8a04",
      low: "#2563eb",
      info: "#71717a",
    });
  });
});

/**
 * W18 — the dark theme, held to the same floors.
 *
 * Every number the `.dark` block's comments quote is asserted here. The
 * block says so, and this is the half that makes that true.
 */
describe("dark theme contrast", () => {
  /** Both grounds a tint can composite over, darkest first. */
  const DARK_GROUNDS = ["background", "card"] as const;
  /** Both steps of the tint scale that carry text. `/40` is border-only. */
  const TINT_STEPS = [0.1, 0.2] as const;

  it.each(STATUSES)(
    "--status-%s-foreground clears AA on its dark subtle surface",
    (name) => {
      expect(
        contrast(dark(`status-${name}-foreground`), dark(`status-${name}-subtle`)),
      ).toBeGreaterThanOrEqual(4.5);
    },
  );

  it.each(SOLID_STATUSES)(
    "--status-%s solid clears the 3:1 non-text floor on the dark canvas",
    (name) => {
      expect(
        contrast(dark(`status-${name}`), dark("background")),
      ).toBeGreaterThanOrEqual(3);
    },
  );

  it.each(RISKS)(
    "--risk-%s-foreground clears AA on every dark tint it can land on",
    (name) => {
      // Light only has to check the densest tint over the darker ground,
      // because darkening the ground is what hurts dark text. In dark the
      // relationship inverts — a denser tint moves the surface *away* from
      // the light foreground on one ground and toward it on the other — so
      // both steps and both grounds get measured.
      const ratios = DARK_GROUNDS.flatMap((ground) =>
        TINT_STEPS.map((alpha) =>
          contrast(
            dark(`risk-${name}-foreground`),
            composite(token(`risk-${name}`), alpha, dark(ground)),
          ),
        ),
      );
      expect(Math.min(...ratios)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it.each(RISKS)(
    "--risk-%s survives the theme as a non-text mark (3:1, both dark surfaces)",
    (name) => {
      // The severity hexes are not redeclared in `.dark` — they are domain
      // semantics, not theme. That only holds if they still read as marks
      // against a dark ground, which is what this measures. `--risk-low` is
      // the tightest at 3.38:1 on a card.
      for (const ground of DARK_GROUNDS) {
        expect(
          contrast(token(`risk-${name}`), dark(ground)),
        ).toBeGreaterThanOrEqual(3);
      }
    },
  );

  it("does not redeclare the severity hexes in the dark block", () => {
    // Stronger than the contrast check above: a future edit that "fixes"
    // dark by lightening `--risk-low` would pass every ratio here and break
    // the one rule CLAUDE.md states outright about these five colours.
    for (const name of RISKS) {
      expect(darkBlock).not.toContain(`--risk-${name}:`);
    }
  });

  it.each([
    ["background", 4.5],
    ["card", 4.5],
    ["muted", 4.5],
    ["topbar", 4.5],
  ] as const)(
    "--muted-foreground clears AA on the dark %s",
    (surface, floor) => {
      expect(
        contrast(dark("muted-foreground"), dark(surface)),
      ).toBeGreaterThanOrEqual(floor);
    },
  );

  it("separates the card from the canvas without leaning on a shadow", () => {
    // The light theme gets this from `--shadow-sm`, which is invisible on a
    // dark ground. Two things have to carry it instead, and both are weaker
    // than a shadow, so both are checked: the surfaces differ at all, and
    // the border is meaningfully more visible than it is in light.
    expect(contrast(dark("card"), dark("background"))).toBeGreaterThan(1.1);
    expect(contrast(dark("border"), dark("card"))).toBeGreaterThan(
      contrast(token("border"), "#ffffff"),
    );
  });

  it("keeps the global bar distinguishable from the canvas", () => {
    // `--topbar: #18181b` measured 1.06:1 against this canvas — the bar
    // vanished. It sits on the card plane now, so what has to be true is
    // that the bar reads as a surface and its border reads as an edge.
    expect(contrast(dark("topbar"), dark("background"))).toBeGreaterThan(1.1);
    expect(contrast(dark("topbar-border"), dark("topbar"))).toBeGreaterThan(1.3);
  });

  it.each([
    ["--topbar-foreground", "topbar-foreground"],
    ["--topbar-muted-foreground", "topbar-muted-foreground"],
    ["--brand-on-ink", "brand-on-ink"],
  ] as const)("%s is readable on the dark bar", (_label, name) => {
    expect(contrast(dark(name), dark("topbar"))).toBeGreaterThanOrEqual(4.5);
  });

  it("lets the mark's teal lead in dark, where teal-600 would only scrape by", () => {
    // The plan predicted the two teals would swap roles (§3.6-(6)) and this
    // is the measurement behind that: on a dark card the interface teal
    // clears AA by 0.13, the mark's teal by 4.89. Asserting the ordering
    // rather than a fixed number, so a retune that keeps the relationship
    // does not fail.
    const onCard = (value: string) => contrast(value, dark("card"));
    expect(onCard(dark("brand"))).toBeGreaterThan(onCard(token("brand")));
    expect(onCard(dark("brand"))).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps body text and the accent readable on the dark active nav row", () => {
    expect(
      contrast(dark("foreground"), dark("brand-subtle")),
    ).toBeGreaterThanOrEqual(4.5);
    expect(contrast(dark("brand"), dark("brand-subtle"))).toBeGreaterThanOrEqual(
      3,
    );
  });

  it("inverts the primary CTA with the same force as light", () => {
    const light = contrast(token("topbar"), "#fafafa");
    const inDark = contrast(dark("primary"), dark("primary-foreground"));
    expect(inDark).toBeGreaterThanOrEqual(4.5);
    // Within a rounding of each other: "the important action" should not
    // shout in one theme and murmur in the other.
    expect(Math.abs(inDark - light)).toBeLessThan(1.5);
  });

  it("keeps the focus ring visible on a dark card", () => {
    // A 2px outline nobody can see is not a focus indicator. 3:1 is the
    // non-text floor and the ring is exactly that kind of mark.
    expect(contrast(dark("ring"), dark("card"))).toBeGreaterThanOrEqual(3);
  });

  it("keeps the destructive red aligned with the severity vocabulary", () => {
    // Light pairs `--destructive` with `--risk-critical`; dark pairs it with
    // `--risk-critical-foreground`, the same red at the lightness dark text
    // needs. Asserting the pairing rather than a literal, so the claim in
    // `index.css` cannot quietly become false.
    expect(token("destructive")).toBe(token("risk-critical"));
    expect(dark("destructive")).toBe(dark("risk-critical-foreground"));
  });

  it("makes `text-destructive` readable in dark, which is why it inverted", () => {
    // The 24 call sites that spell an error sentence with this token. On a
    // dark card the light theme's #dc2626 measures 3.62:1 — under AA, and
    // invisible in review because every one of them looks right in light.
    for (const surface of DARK_GROUNDS) {
      expect(
        contrast(dark("destructive"), dark(surface)),
      ).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("keeps a destructive button's own label legible", () => {
    // The same token is also a fill. Inverting it means the label on top has
    // to invert too, or the button goes bright-on-white.
    expect(
      contrast(dark("destructive"), dark("destructive-foreground")),
    ).toBeGreaterThanOrEqual(4.5);
  });
});
