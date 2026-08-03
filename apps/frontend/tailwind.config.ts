import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

/**
 * Tailwind config — W11-A token expansion.
 *
 * The actual token VALUES live in `src/index.css` as CSS custom properties
 * (shadcn convention). This file maps those CSS vars to Tailwind utility
 * classes so components can write `bg-card`, `shadow-md`, `duration-fast`
 * etc. without ever touching a hex literal.
 *
 * What changed in W11-A:
 *   - Radius gained a sm/md/lg/xl hierarchy (was: md derived from --radius).
 *   - boxShadow now reads from --shadow-sm / --shadow-md / --shadow-lg.
 *   - transitionDuration exposes the Linear-polish 150/200/250 ms scale.
 *   - transitionTimingFunction adds `ease-out` (cubic-bezier) from tokens.
 *
 * What did NOT change:
 *   - Risk severity color tokens (Critical / High / Medium / Low / Info).
 *   - Inter + JetBrains Mono font stack.
 *   - Layout density vars (sidebar / header / row).
 *
 * Dark mode is wired up (`darkMode: ["class"]`) but no `.dark` tokens are
 * populated in W11 — v2.5+ trail. Components should NOT use `dark:` here.
 */
/**
 * Wrap each `--risk-*` custom property so Tailwind's opacity modifier reaches
 * it. See the `risk:` block below for why a bare `var()` cannot be used.
 */
function riskTint(tokens: Record<string, string>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(tokens).map(([key, cssVar]) => [
      key,
      `color-mix(in srgb, var(${cssVar}) calc(<alpha-value> * 100%), transparent)`,
    ]),
  );
}

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class"],
  theme: {
    container: {
      center: true,
      padding: "1rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        // Scrim behind a dialog or drawer. Its own token because a scrim is
        // darker than what it covers in BOTH themes, which `bg-foreground/40`
        // stops being the moment foreground is near-white (W18).
        overlay: "hsl(var(--overlay))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // popover: the CSS vars (--popover / --popover-foreground) exist in
        // index.css but were never mapped here, so `bg-popover` resolved to
        // nothing and the DropdownMenu (release switcher) rendered transparent.
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        // Risk severity tokens (unchanged — domain semantics fixed).
        // `foreground` variants (G0-1) are for text ON a risk tint, e.g.
        // `bg-risk-medium/10 text-risk-medium-foreground`.
        //
        // G0-7 — why these are wrapped in `color-mix` rather than written as
        // a bare `var()`.
        //
        // Tailwind implements `bg-risk-high/10` by splitting the colour into
        // channels and re-emitting it with an alpha. A bare `var(--risk-high)`
        // gives it nothing to split, so it does not emit a degraded rule — it
        // emits NO rule for the slashed class, and every element written that
        // way falls back to transparent. That was true of 27 call sites across
        // 13 files until G0-7, in a spelling the design-system doc described
        // as working.
        //
        // `<alpha-value>` is the documented hook: Tailwind substitutes the
        // modifier (or `1` when there is none) into whatever expression is
        // here, so `color-mix` composites the severity hex against
        // `transparent` at exactly the requested alpha. The hex in
        // `index.css` stays the single source of the hue — charts still read
        // it raw — and the whole `risk` family is covered, including the
        // `-foreground` shades, so no member of it can regress to a
        // silently-dropped rule.
        //
        // `riskTintOpacity.test.ts` compiles this config and fails if any
        // step stops emitting a rule.
        risk: riskTint({
          critical: "--risk-critical",
          "critical-foreground": "--risk-critical-foreground",
          high: "--risk-high",
          "high-foreground": "--risk-high-foreground",
          medium: "--risk-medium",
          "medium-foreground": "--risk-medium-foreground",
          low: "--risk-low",
          "low-foreground": "--risk-low-foreground",
          info: "--risk-info",
          "info-foreground": "--risk-info-foreground",
        }),
        // Brand accent (W14) — product identity, applied to a whitelist.
        brand: {
          DEFAULT: "var(--brand)",
          subtle: "var(--brand-subtle)",
          "on-ink": "var(--brand-on-ink)",
        },
        // Global bar (W14) — the dark surface at the top of the shell.
        topbar: {
          DEFAULT: "var(--topbar)",
          foreground: "var(--topbar-foreground)",
          "muted-foreground": "var(--topbar-muted-foreground)",
          border: "var(--topbar-border)",
          accent: "var(--topbar-accent)",
        },
        // Status surfaces (G0-1) — operation / entity state. See index.css
        // for the token contract (solid = non-text ≥3:1, foreground = text
        // ≥4.5:1, border = decorative).
        status: {
          success: {
            DEFAULT: "var(--status-success)",
            subtle: "var(--status-success-subtle)",
            border: "var(--status-success-border)",
            foreground: "var(--status-success-foreground)",
          },
          warning: {
            subtle: "var(--status-warning-subtle)",
            border: "var(--status-warning-border)",
            foreground: "var(--status-warning-foreground)",
          },
          danger: {
            subtle: "var(--status-danger-subtle)",
            border: "var(--status-danger-border)",
            foreground: "var(--status-danger-foreground)",
          },
          info: {
            subtle: "var(--status-info-subtle)",
            border: "var(--status-info-border)",
            foreground: "var(--status-info-foreground)",
          },
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      spacing: {
        sidebar: "var(--layout-sidebar)",
        header: "var(--layout-header)",
        topbar: "var(--layout-topbar)",
        row: "var(--table-row)",
      },
      borderRadius: {
        // W11-A — radius hierarchy. Different sizes for different affordances.
        //
        //   sm  4 px  — chips / small inputs
        //   md  6 px  — buttons / cards / table chrome (= --radius default)
        //   lg  8 px  — drawer, large panels
        //   xl 12 px  — modals, dialogs
        //
        // shadcn's older `rounded-lg`/`rounded-md`/`rounded-sm` mapping
        // (lg = --radius, md = lg-2, sm = lg-4) still works for existing
        // components because we re-declare those three keys explicitly.
        sm: "calc(var(--radius) - 2px)",
        md: "var(--radius)",
        lg: "calc(var(--radius) + 2px)",
        xl: "calc(var(--radius) + 6px)",
      },
      boxShadow: {
        // Subtle, Vercel-style elevation. The shadcn default uses Tailwind's
        // generic shadow tokens; we route through CSS vars so a future dark
        // theme can shift to ring-based elevation by changing one place.
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
      },
      transitionDuration: {
        // Linear polish — three named steps.
        fast: "var(--duration-fast)",
        base: "var(--duration-base)",
        slow: "var(--duration-slow)",
      },
      transitionTimingFunction: {
        // `cubic-bezier(0.16, 1, 0.3, 1)` — Linear-style ease-out with a
        // gentle overshoot decay. Pairs well with the 150 / 200 / 250 ms
        // durations above.
        "ease-out-soft": "var(--ease-out)",
      },
    },
  },
  plugins: [animate],
};

export default config;
