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
        risk: {
          critical: "var(--risk-critical)",
          high: "var(--risk-high)",
          medium: "var(--risk-medium)",
          low: "var(--risk-low)",
          info: "var(--risk-info)",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      spacing: {
        sidebar: "var(--layout-sidebar)",
        header: "var(--layout-header)",
        row: "var(--table-row)",
      },
      borderRadius: {
        // W13 — radius hierarchy, one step rounder than W11 (--radius 8px).
        //
        //   sm  6 px  — chips / small inputs
        //   md  8 px  — cards / inputs / table chrome (= --radius default)
        //   lg 10 px  — drawer, large panels
        //   xl 14 px  — modals, dialogs
        //
        // Buttons are pills (`rounded-full` in button.tsx), not part of
        // this scale.
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
