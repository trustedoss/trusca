---
id: design-system
title: Design system
description: TRUSCA design system — tokens (colour, spacing, radius, shadow, motion, typography), component conventions, micro-interactions, accessibility, and the W11 visual-identity refresh.
sidebar_label: Design system
sidebar_position: 10
---

# Design system

The portal frontend follows a single, light-mode design system inspired by **Vercel** (light base — surfaces, dense rows, sidebar tint) and **Linear** (typography hierarchy, motion, focus polish). Dark mode is deferred to +.

:::note Audience
Frontend contributors, designers, and reviewers. The tokens here are the canonical reference — components should never hard-code hex values or magic spacing.
:::

This page is the single source of truth for visual decisions. The implementation lives in:

- `apps/frontend/src/index.css` — CSS custom properties (`--background`, `--ring`, `--risk-critical`, …)
- `apps/frontend/tailwind.config.ts` — Tailwind tokens derived from the CSS variables
- `apps/frontend/src/components/ui/` — shadcn/ui primitives wired to the tokens

## Philosophy

TRUSCA is a **risk-first, information-dense, modern enterprise SCA** tool. The visual identity must:

1. **Communicate severity at a glance.** Risk colours (Critical / High / Medium / Low / Info) appear next to a textual label and an icon or dot — colour is never the sole signal.
2. **Pack data without feeling cramped.** Compact 40 px table rows; 256 px sidebar; 56 px global bar; 16 / 20 / 24 px card padding scale.
3. **Read as a modern enterprise product.** Warm near-black (`#18181b`) instead of navy (`#0f172a`); off-white canvas (`#fafafa`) so cards lift visually; subtle shadows; semibold headings; visible focus rings.
4. **Move only as much as needed.** Motion is short and ease-out — 150 ms for hover / focus, 200 ms for drawer slide, 250 ms for page-level chrome. No bounce, no fade-in delays.

### W11 (2026-05-27) — visual refresh

The W11 milestone replaced the previous "enterprise-style 2015" aesthetic with the current Vercel+Linear blend. The structural decisions (sidebar nav, 40 px row, drawer-for-detail, risk semantics) carry over unchanged. What changed:

| Surface | Before | After |
|---|---|---|
| Primary CTA | `#0f172a` cool navy | `#18181b` warm near-black |
| Page background | `#ffffff` pure white | `#fafafa` off-white canvas |
| Card surface | grey-tinted | `#ffffff` pure white (lifts off the canvas) |
| Border | `slate-200` | `#e5e5ea` neutral hairline |
| Radius | 8 px uniform | hierarchy — sm 4 / md 6 / lg 8 / xl 12 |
| Shadow | none / default | sm (card) / md (popover) / lg (drawer · dialog) |
| Motion | default browser | 150 / 200 / 250 ms ease-out |
| Heading weight | bold | semibold + tracking-tight |
| Focus ring | shadcn default | 2 px outline + 2 px offset (a11y) |
| Detail surface | drawer-only | dual surface — drawer (quick check) + page nav (deep work) |

The risk colour palette (Critical / High / Medium / Low / Info) is intentionally **unchanged** — the brand semantics are fixed across releases. Where the raw severity hex fails WCAG AA as body text on a light tint, the badge text shade is darkened within the same hue family (see [Severity colour accessibility](#severity-colour-accessibility) below).

## Colour tokens

All colour decisions reference the CSS custom properties declared in `index.css`. Components should never reference hex values directly — use the Tailwind utility (`bg-background`, `text-foreground`, `bg-risk-critical/10`) or the CSS variable.

### Neutral palette (Vercel base)

| Token | Hex | HSL | Use |
|---|---|---|---|
| `--background` | `#fafafa` | `0 0% 98%` | Page canvas. Lets cards lift visually. |
| `--card` | `#ffffff` | `0 0% 100%` | Elevated surfaces — cards, popovers, drawer body, tooltip. |
| `--foreground` | `#18181b` | `240 6% 10%` | Body text. Warm near-black, not navy. |
| `--muted` | `#f4f4f5` | `240 5% 96%` | Subtle fills — table headers, sidebar tint, placeholder backgrounds, disabled inputs. |
| `--muted-foreground` | `#6c6c75` | `240 4% 44%` | Secondary text, captions, table column headers. |
| `--border` | `#e7e7e9` | `240 5% 91%` | Hairline borders. Decorative separator only — never the sole means of identifying a UI region. |
| `--input` | `#e7e7e9` | `240 5% 91%` | Input outline. |
| `--primary` | `#18181b` | `240 6% 10%` | Primary CTA — "the important action on the page". |
| `--primary-foreground` | `#fafafa` | `0 0% 98%` | Text on primary. |
| `--destructive` | `#dc2626` | `0 72% 50.6%` | Destructive CTA. Aligned with `--risk-critical` so destructive buttons share severity-badge visual language. The decimal is load-bearing — `51%` renders `#dc2828`, which is not that hex. |
| `--destructive-foreground` | `#fafafa` | `0 0% 98%` | Text on destructive. |
| `--ring` | `#18181b` | `240 6% 10%` | Focus ring. Matches primary so the outline reads as "the same action this is". |
| `--overlay` | `#18181b` | `240 6% 10%` | Scrim behind a dialog or drawer, used at 40 %. Its own token rather than `--foreground`, because a scrim is darker than what it covers in both themes and text is not. |

### Risk severity (domain semantics — fixed)

| Token | Hex | Use |
|---|---|---|
| `--risk-critical` | `#dc2626` | Critical CVE, forbidden licence, build-blocking finding. |
| `--risk-high` | `#ea580c` | High-severity CVE, conditional licence at risk. |
| `--risk-medium` | `#ca8a04` | Medium CVE, conditional licence awaiting review. |
| `--risk-low` | `#2563eb` | Low CVE, informational status. |
| `--risk-info` | `#71717a` | Neutral informational state. |

The severity hex values are **never changed** between releases. They appear in:

- Recharts fills and chart legends (raw hex via the `--risk-X` variable).
- `bg-risk-X/N` tints on badges, callouts and grid cells.
- Border accents (`border-risk-high/40` on buttons and alerts).

The tint scale has exactly three steps, and a surface should use no other:

| Step | Use |
|---|---|
| `/10` | The tinted surface — the default for any callout, badge or cell. |
| `/20` | The same surface emphasised: a hover state, or a chip nested on `/10`. |
| `/40` | The border on either. |

The opacity modifier works because `tailwind.config.ts` wraps each severity token in a `color-mix()` carrying Tailwind's `<alpha-value>`. A bare `var()` does not work — Tailwind cannot split it into channels, and rather than degrading it emits no rule at all, so the element renders transparent. Every `bg-risk-X/N` in the app painted nothing until G0-7 repaired the mechanism. `apps/frontend/tests/unit/design/riskTintOpacity.test.ts` compiles the config and fails if any step stops emitting a rule.

#### Risk tint foregrounds

Severity fills are chart colours; text sitting **on** a severity tint needs its own darker shade. Each severity therefore ships a `-foreground` companion. Use it — do not reach for a Tailwind palette class, which is how the same state ended up rendered as `text-yellow-800` in one file and `text-amber-900` in the next.

Contrast is quoted against the worst case a foreground has to survive: the densest tint (`/20`) composited over the page ground (`--background`, the darker of the two surfaces a tint sits on). On the ordinary `/10` step over a card each figure is roughly 1 point higher.

| Token | Hex | Contrast on `bg-risk-X/20` |
|---|---|---|
| `--risk-critical-foreground` | `#b91c1c` | 4.55:1 |
| `--risk-high-foreground` | `#9a3412` | 5.51:1 |
| `--risk-medium-foreground` | `#854d0e` | 5.43:1 |
| `--risk-low-foreground` | `#1d4ed8` | 4.86:1 |
| `--risk-info-foreground` | `#52525b` | 5.80:1 |

```tsx
// Do
<Badge className="bg-risk-medium/10 text-risk-medium-foreground">KEV</Badge>
// Don't — the severity hex is not readable on its own tint (3.05:1 for high)
<Badge className="bg-risk-medium/10 text-risk-medium">KEV</Badge>
// Don't — shade drifts per file, and nothing checks its contrast
<Badge className="bg-risk-medium/10 text-yellow-800">KEV</Badge>
```

`statusTokenContrast.test.ts` asserts both halves: that each foreground clears AA on its densest tint, and that the severity token itself does **not** — so "why not just use `text-risk-high`?" answers itself with a red test.

See [Severity colour accessibility](#severity-colour-accessibility).

### Brand accent (W14)

The mark has carried teal since the rebrand, but it never reached the interface — leaving the product neutral-on-neutral. The accent now has tokens, applied to a **whitelist** rather than everywhere: active sidebar row, active tab underline, progress indicator, empty states, and the login gateway.

| Token | Hex | Use | Contrast |
|---|---|---|---|
| `--brand` | `#0d9488` | Indicators, progress, active marks | 3.74:1 on `--card` (UI, ≥3) |
| `--brand-subtle` | `#f0fdfa` | Tinted ground for the active row | body text 16.99:1 on it |
| `--brand-on-ink` | `#2dd4bf` | The mark's own teal — **ink surfaces only**: the global bar and the gateway's brand panel | 9.52:1 on `--topbar`, 2.49:1 on white |

Two teals, because one cannot do both jobs: the logo's teal is too light for white surfaces, and the darker interface teal disappears on the ink bar.

`--brand-strong` (teal-700, accent as text) was declared in W14 and removed again: nothing painted with it, and `tokenConsumers.test.ts` fails on a token no call site uses — an unused colour is vocabulary whose contrast has never been checked against a real surface. Declare it again when a call site needs it, and measure it then.

**Never accented**: the risk scale, `--destructive`, the primary CTA, and the focus ring. Severity is domain meaning; tinting it with brand would blur the signal. And in every case above the accent is a *second* marker — the active row keeps an ink label, the active tab keeps an ink title — so state never depends on colour alone.

### Global bar (W14)

The shell's top edge spans the full width, above both the sidebar and the content. It is an ink surface inside a light theme, so it carries its own foreground scale; borrowing the page's would put near-black text on near-black.

| Token | Hex | Use |
|---|---|---|
| `--topbar` | `#18181b` | Bar background. Matches `--primary`, so the bar and the primary CTA read as one material. |
| `--topbar-foreground` | `#fafafa` | Primary text and icons — 16.97:1 |
| `--topbar-muted-foreground` | `#a1a1aa` | Secondary text — 6.91:1 |
| `--topbar-border` | `#27272a` | Bottom edge, control outlines |
| `--topbar-accent` | `#27272a` | Hover / chip grounds on the bar |

Components that appear both on the bar and on a page (the bell, the language toggle, the ⌘K trigger, the brand mark) take an `onInk` prop rather than being duplicated.

### Status surfaces (operation / entity state)

Distinct from severity: a scan is *running*, a gate *passed*, a credential is *present*. These are lifecycle states, not finding severities, and they get their own family so callouts and pills never reach for a raw palette class.

Every status exposes four tokens:

| Suffix | Contract |
|---|---|
| `--status-X` | Solid fill for **non-text** marks (dots, bars) — WCAG 1.4.11, ≥ 3:1 on `--background`. |
| `--status-X-subtle` | Tinted surface background. |
| `--status-X-border` | Border on that tint. Decorative — the tint plus foreground already carry the meaning, so it is deliberately not contrast-gated. |
| `--status-X-foreground` | Text on that tint — WCAG AA, ≥ 4.5:1. |

| Status | Solid | Subtle | Border | Foreground | Text contrast |
|---|---|---|---|---|---|
| `success` | `#059669` | `#ecfdf5` | `#6ee7b7` | `#047857` | 5.21:1 |
| `warning` | — | `#fffbeb` | `#fcd34d` | `#92400e` | 6.84:1 |
| `danger` | `#dc2626` | `#fef2f2` | `#fca5a5` | `#b91c1c` | 5.91:1 |
| `info` | — | `#eff6ff` | `#93c5fd` | `#1d4ed8` | 6.16:1 |

A solid is declared only where something paints a non-text mark with it — the finished-scan progress bar for `success`, the dashboard trend's "new findings" bars for `danger`. `tokenConsumers.test.ts` fails on a declared token nothing consumes, because a colour nobody paints with is a contrast promise nobody has checked against a real surface. When a warning dot appears, declare that solid then and measure it then; amber-600 will not do, having measured 3.05:1 against `#fafafa`, a knife-edge over the 3:1 floor.

`--status-danger` deliberately equals `--risk-critical` and the destructive hex. The vocabularies stay separate in *name* because they answer different questions — a finding's severity versus an operation's state — but a red meaning "this went badly" should not be a second, slightly different red.

```tsx
<Badge
  variant="outline"
  className="border-status-success-border bg-status-success-subtle text-status-success-foreground"
>
  Succeeded
</Badge>
```

:::note Enforced at PR time
`npm run token:lint` fails on any new raw hex or Tailwind palette class under `src/`, and the recorded baseline can only shrink. Contrast for every token above is asserted in `tests/unit/design/statusTokenContrast.test.ts`, so retuning a token without re-checking it fails the build. Definition files (`index.css`), brand marks and vendor icons are exempt.
:::

## Dark theme (W18)

Slate, not zinc. The light theme is zinc — hue 240, a neutral grey — and mirroring it darker lands on the dark palette that ships with every shadcn application. This one takes the hue of the brand's dark slate `#0f172a`, so the theme reads as this product rather than as "dark mode was switched on".

Selected with the `.dark` class on `<html>`. The preference is resolved by an inline script in `index.html` before the bundle loads, because resolving it in React means the first painted frame is light and the app flashes white on every reload. Three states — light, dark, and follow-the-system — cycled from the global bar.

### What the theme does not change

- **The five severity hexes.** They are domain semantics, not decoration (`Severity 색 변경 0`, W11). On both dark surfaces they still clear the 3:1 that WCAG 1.4.11 asks of a non-text mark; the tightest is `--risk-low` on `--card` at 3.38:1.
- **Layout, radius and motion tokens.** A theme has no opinion about geometry or timing.

### What a light theme gets for free

Two things carry over silently in light and have to be earned in dark.

A **shadow** lifts a card off the canvas in light. On a dark ground it is a dark smudge and does nothing, so `--border` carries the card edge instead: 1.51:1 against `--card` here, against 1.23:1 in light. The border is doing a job it only decorated before.

**Text on a tint** flips direction. Light darkens the severity hue; dark has to lighten it, so the `-foreground` shades are the 400-level ones. Worst case across both grounds and both tint steps is 5.49:1.

### Neutral palette

| Token | Dark | HSL | Note |
|---|---|---|---|
| `--background` | `#080c16` | `223 47% 6%` | Canvas, below the card. |
| `--foreground` | `#f1f5f9` | `210 40% 96%` | Body text — 17.84:1 on the canvas. |
| `--card` | `#11192d` | `221 46% 12%` | The brand's dark slate. Raised. |
| `--card-foreground` | `#f1f5f9` | `210 40% 96%` | |
| `--popover` | `#17213a` | `223 43% 16%` | Above the card, so a dialog over a card over the page reads as three layers without a shadow. |
| `--popover-foreground` | `#f1f5f9` | `210 40% 96%` | |
| `--muted` | `#1c2740` | `222 39% 18%` | Table headers, sidebar tint, disabled inputs. |
| `--muted-foreground` | `#94a3b8` | `215 20% 65%` | 7.62:1 on the canvas, 6.76:1 on a card, 5.79:1 on `--muted` — the surface the light theme was caught out on. |
| `--border` | `#2d3b53` | `218 30% 25%` | Carries the card edge. |
| `--input` | `#2d3b53` | `218 30% 25%` | |
| `--primary` | `#f1f5f9` | `210 40% 96%` | Inverted: near-white on card slate, 16.30:1, within a rounding of light's 16.97:1. |
| `--primary-foreground` | `#11192d` | `221 46% 12%` | |
| `--secondary` | `#1c2740` | `222 39% 18%` | |
| `--secondary-foreground` | `#f1f5f9` | `210 40% 96%` | |
| `--accent` | `#1c2740` | `222 39% 18%` | |
| `--accent-foreground` | `#f1f5f9` | `210 40% 96%` | |
| `--destructive` | `#f87171` | `0 90.6% 70.8%` | Inverted. See below. |
| `--destructive-foreground` | `#11192d` | `221 46% 12%` | |
| `--ring` | `#94a3b8` | `215 20% 65%` | 6.76:1 on a card — a 2 px outline nobody can see is not a focus indicator. |
| `--overlay` | `#000000` | `0 0% 0%` | Pure black, not the canvas: the canvas is already dark, so a 40 % scrim made of it would barely register. |

`--destructive` is two things at once — the fill of a destructive button and, through shadcn's `text-destructive`, the colour of an error sentence. `#dc2626` does both in light; on a dark card it measures 3.62:1 as text. Lightening it fixes the text and keeps the fill idiomatic, and the alignment survives: light pairs it with `--risk-critical`, dark with `--risk-critical-foreground` — the same red at the lightness dark text needs.

### Risk tint foregrounds

The severity fills are unchanged; only the text shade inverts.

| Token | Dark | Worst measured |
|---|---|---|
| `--risk-critical-foreground` | `#f87171` | 5.49:1 |
| `--risk-high-foreground` | `#fb923c` | 6.20:1 |
| `--risk-medium-foreground` | `#facc15` | 8.54:1 |
| `--risk-low-foreground` | `#60a5fa` | 5.66:1 |
| `--risk-info-foreground` | `#a1a1aa` | 5.51:1 |

Worst measured is across both dark grounds and both tint steps that carry text (`/10`, `/20`).

:::note Icons keep the severity hex
If it draws letters it takes the `-foreground`; if it is an icon or a decorative `aria-hidden` symbol it keeps the severity hex. Text owes AA on whatever it sits on — `text-risk-medium` measures 2.86:1 on a white card, so some of those call sites were under AA in light before dark existed. A mark owes 3:1, clears it on every surface in both themes, and being recognisable is the point of it.
:::

### Status surfaces

| Status | Solid | Subtle | Border | Foreground | Text contrast |
|---|---|---|---|---|---|
| `success` | `#059669` | `#052e22` | `#15803d` | `#4ade80` | 8.48:1 |
| `warning` | — | `#2e2205` | `#a16207` | `#facc15` | 10.18:1 |
| `danger` | `#dc2626` | `#3a0d0d` | `#b91c1c` | `#f87171` | 6.11:1 |
| `info` | — | `#0a1a2e` | `#1d4ed8` | `#60a5fa` | 6.88:1 |

The two declared solids do not move and still clear 3:1 as non-text marks (success 5.19:1, danger 4.05:1). Borders stay decorative and ungated, as in light.

### Brand accent and the global bar

| Token | Dark | Note |
|---|---|---|
| `--brand` | `#2dd4bf` | The mark's own teal leads in dark: 9.39:1 on a card, against teal-600's 4.63:1, which clears AA but only barely and reads muddy next to slate. |
| `--brand-subtle` | `#0b2b28` | Active nav ground — body text 13.79:1, teal 8.11:1. |
| `--brand-on-ink` | `#2dd4bf` | The bar is dark in both themes, so here the two teals simply agree. |
| `--topbar` | `#17213a` | On the popover plane. `#18181b` measured 1.06:1 against this canvas — the bar vanished. |
| `--topbar-foreground` | `#f1f5f9` | |
| `--topbar-muted-foreground` | `#94a3b8` | |
| `--topbar-border` | `#2d3b53` | 1.55:1 against the bar — what separates it from the canvas. |
| `--topbar-accent` | `#1c2740` | Hover ground inside the bar. |

The two teals swap which one leads, which the plan predicted. The names still hold: `--brand` is "the accent, whatever the theme decides that is", and `--brand-on-ink` is "the teal on the bar".

### Mirroring scope

TRUSCA and BomLens share a lineage and are reviewed against each other. That review has a boundary, and it runs here:

- **Neutral tokens are family assets.** Canvas, card, border, muted, the shadcn semantic set — these are conventions, and matching them is not imitation.
- **The accent, the dark palette and the shell skeleton are product identity.** The teal, the slate dark theme, the ink global bar, the sidebar grouping. These are deliberately not mirrored in either direction, and a parity review that proposes converging them is proposing to delete the difference the product is being built to have.

`bomlens-parity-review.md` carries the same line in one sentence.

:::note Enforced at PR time
`tests/unit/design/tokenDocParity.test.ts` compares every table above against the `.dark` block in `index.css` — in both directions, and against the Korean mirror. `tokenFormatContract.test.ts` checks that each token is written in the format `tailwind.config.ts` reads it in: a hex where the config says `hsl(var(--x))` produces `hsl(#f87171)`, which is not a colour, and the element renders with no fill at all. Both gates were written after that exact defect nearly shipped.
:::

## Spacing

| Token | Value | Use |
|---|---|---|
| `--layout-sidebar` | 256 px | Expanded sidebar width (default). |
| `--layout-topbar` | 56 px | Global bar height — the shell's top edge, full width above the sidebar. |
| `--layout-sidebar-collapsed` | 64 px | Icon-only rail width when the user collapses the sidebar (≥`lg`). |
| `--layout-header` | 48 px | In-page `PageHeader` row height. Not the shell's top edge — that is `--layout-topbar`. |
| `--table-row` | 40 px | Compact table row height. |

**Sidebar behaviour.** The left sidebar is **user-collapsible and viewport-responsive**:

- **≥ `lg` (1024 px):** fixed sidebar. A toggle at the bottom of the rail collapses it from 256 px to a 64 px icon-only rail; collapsed labels move to `aria-label` + native hover tooltip. The choice persists across reloads (`uiStore` → `localStorage` key `trustedoss-ui`). Width animates over `--duration-base`.
- **< `lg`:** the fixed sidebar is hidden and a header hamburger opens an overlay drawer (left-side `Sheet`) carrying the full-label nav. The drawer closes on navigate, overlay click, or ESC.

**Card padding** standardises to **16 / 20 / 24 px** (Tailwind `p-4` / `p-5` / `p-6`):

- `p-4` — compact cards (dashboard tiles, stat cards).
- `p-5` — standard cards (project list rows, drawer sections).
- `p-6` — primary content cards (page-level wrappers, dialogs).

## Radius hierarchy

Different affordances use different radii so depth reads at a glance.

| Token | Value | Affordance |
|---|---|---|
| `--radius-sm` | 4 px | Small inputs, badges, chips. |
| `--radius` | 6 px | **Default** — buttons, cards, table chrome. |
| `--radius-lg` | 8 px | Drawer, large surfaces. |
| `--radius-xl` | 12 px | Modals, dialogs. |

The Tailwind config derives `rounded-sm`, `rounded-md`, `rounded-lg`, `rounded-xl` from these tokens via `calc()`.

## Shadow scale

Vercel-style subtle elevation. Light shadows only — no glow.

| Token | Value | Use |
|---|---|---|
| `--shadow-sm` | `0 1px 2px 0 rgb(0 0 0 / 0.04)` | Cards, stat tiles. |
| `--shadow-md` | `0 2px 8px -2px rgb(0 0 0 / 0.08), 0 1px 2px 0 rgb(0 0 0 / 0.04)` | Dropdown, popover, tooltip. |
| `--shadow-lg` | `0 10px 28px -8px rgb(0 0 0 / 0.12), 0 3px 8px -3px rgb(0 0 0 / 0.06)` | Drawer, dialog. |

## Motion

Short, ease-out — Linear polish. Three steps cover the majority of UI animation.

| Token | Value | Use |
|---|---|---|
| `--duration-fast` | 150 ms | Hover state, focus ring fade-in, badge tint shift, button colour transition. |
| `--duration-base` | 200 ms | Drawer slide, popover open, dropdown reveal. |
| `--duration-slow` | 250 ms | Page-level chrome transitions, route change entrance. |
| `--ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` | Single easing curve used everywhere. Snappy in, gentle out. |

**Loading states are skeletons**, not spinners. Long async work (scans, exports) shows a labelled progress bar — never a bare spinner.

## Typography

| Element | Family | Size / Weight | Notes |
|---|---|---|---|
| Body | Inter | 14 px / regular | `letter-spacing: −0.005em` (Linear tighter body). |
| Heading 1 / 2 / 3 / 4 | Inter | 18 ~ 24 px / semibold | `tracking-tight`. Never bold — semibold reads more "modern enterprise". |
| Mono | JetBrains Mono | 13 px | Code, hashes, CVE IDs, PURLs, JSON snippets. `letter-spacing: 0` — mono does not inherit body tightening. |

OpenType features `rlig` and `calt` are enabled on `body` for proper Inter rendering.

**Use the typography primitives, not raw utilities.** `apps/frontend/src/components/ui/typography.tsx` exposes the scale as named components so a given role is identical on every screen instead of drifting (`text-lg` here, `text-base` there):

| Component | Element | Role |
|---|---|---|
| `PageTitle` | `h1` | The single page title — 18 px semibold tracking-tight. |
| `SectionTitle` | `h2` | Section / sub-area heading — 16 px semibold. |
| `Subtitle` | `p` | Muted line beneath a page title — 14 px. |
| `Body` | `p` | Body copy — 14 px (`muted` prop for secondary copy). |
| `Caption` | `span` | Dense meta (timestamps, counts) — 12 px muted. |
| `Eyebrow` | `span` | Uppercase overline / column-group label — 12 px medium. |

Reach for a raw `text-*` utility only for one-off inline spans that no primitive covers; never hand-roll a page title.

## Focus ring

Every interactive element shows a visible focus ring on keyboard navigation:

```css
focus-visible:outline-none
focus-visible:ring-2
focus-visible:ring-ring
focus-visible:ring-offset-2
```

`--ring` matches `--primary`, so the outline reads as the same colour family as the action. `ring-offset-2` adds a 2 px breathing gap so the ring is legible against tinted backgrounds (severity badges, alert cards).

**Never disable the focus ring.** The 2 px outline is the keyboard user's primary affordance — removing it makes the UI unreachable.

## Component conventions

The portal builds on [shadcn/ui](https://ui.shadcn.com/) primitives. Each primitive is wired to the design tokens above and re-exported from `apps/frontend/src/components/ui/`.

### Page header

`apps/frontend/src/components/PageHeader.tsx`

Every route renders its header through `PageHeader` so the title typography and header chrome are identical. Chrome is unified to `bg-background` + `border-b` (off-white canvas with a hairline divider) so the white cards / tables below read as raised. Two archetypes:

- `variant="stacked"` (default) — taller header (`py-4`) with a `PageTitle` and a muted `description`. For pages that need an explanatory line (Scans, Admin sections).
- `variant="bar"` — slim 48 px row (`var(--layout-header)`), title plus an optional right `actions` slot (buttons or meta text), no subtitle. For dense pages whose purpose is self-evident (Dashboard, Project list).

The stacked variant also takes an optional `meta` slot — a block under the description (e.g. a "last updated 2m ago" line with its own test id), kept separate from `description` so block content is not nested inside the subtitle `<p>`. The `actions` slot is caller-owned markup, so existing harness `data-testid`s on buttons / meta are preserved.

Do not hand-roll a `<header><h1>` block — extend `PageHeader` if a new layout is genuinely needed. **Exception:** the detail pages (Project detail, Component / Vulnerability detail, Compare, Scan detail) use a *breadcrumb header* — a `<nav>` breadcrumb plus a contextual title — which is a distinct archetype `PageHeader` does not model yet. Those pages keep their hand-rolled header but still draw type from the same scale.

### Button

`apps/frontend/src/components/ui/button.tsx`

- Default variant uses `bg-primary text-primary-foreground` — solid warm near-black.
- `outline` variant uses `border-input bg-background` — for secondary actions.
- `ghost` variant uses no background, hover tint only — for nav items and toolbar actions.
- `destructive` uses `bg-destructive` — Critical-aligned red, reserved for irreversible actions (delete, revoke, reject).
- Hover and focus transitions use `transition-colors duration-fast ease-out` (150 ms).
- All variants include the focus ring.

### Input / Select / Checkbox

- Border colour `--input`, focus ring `--ring`.
- Disabled state uses `bg-muted text-muted-foreground`.
- Error state uses `border-destructive` plus an `aria-live="polite"` message under the field.

### Card

- Pure-white surface (`bg-card`) on the off-white canvas — lifts visually without a heavy shadow.
- `rounded-md` (6 px) by default; `rounded-lg` (8 px) for primary content cards.
- `shadow-sm` for stats / tiles; `shadow-md` for elevated popovers.

### Table

- Compact density — row height 40 px, header tint `bg-muted`.
- Sortable column headers show a 12 px chevron next to the label.
- Row hover uses `bg-muted/50` with a 150 ms transition.
- For 1 k+ rows use virtual scrolling (`react-virtuoso`).
- Severity columns always pair the colour with a text label or icon — see SeverityBadge.

### Drawer (`sheet.tsx`)

- Right-side slide-in, **width 480 ~ 640 px** depending on content density.
- `shadow-lg` for the drawer panel.
- 200 ms `ease-out` slide.
- Drawer state is **URL-encoded** (`?drawer=component:abc123`) so it survives reload.
- Use the drawer for **quick checks** — a tabular row's full payload, a CVE's CVSS breakdown, a component's licence chain. Use page navigation for **deep work** — bulk edit, multi-step approval, scan configuration.

### Dialog

- Modal centred over a `bg-foreground/40` backdrop.
- `rounded-xl` (12 px), `shadow-lg`.
- Reserved for **destructive confirmations** (delete project, revoke API key) and **inline create flows** (new project, new team).

### EmptyState

`apps/frontend/src/components/EmptyState.tsx`

- Centre-aligned, max-width 420 px.
- Layered icon medallion (W12-D) — two soft concentric muted rings behind a raised white inner disc holding the icon — then title (semibold), description (muted), single primary CTA. Pass `illustration` to swap the medallion for a richer inline SVG (inline only, no new asset).
- Used for: empty list, empty search result, empty drawer tab, first-time onboarding card.

### Skeleton

`apps/frontend/src/components/ui/skeleton.tsx` · `skeletons.tsx`

- `Skeleton` is the base bar (`animate-pulse`, `rounded-sm`). Prefer composite skeletons that mirror the final layout over a single full-width bar so content settles in without reflow.
- `TableRowsSkeleton` renders per-column cells (one width per column) for loading tables. The table keeps `aria-busy`; skeleton rows are `aria-hidden`.

### Badge

`apps/frontend/src/components/ui/badge.tsx`

Risk-tinted variants pair a status word with the design-system colour. Background uses `bg-risk-X/10` so the chip reads as a coloured tint. Text uses the severity's `-foreground` token so the rendered contrast clears WCAG AA 4.5:1 — see [Severity colour accessibility](#severity-colour-accessibility).

### Toast

`apps/frontend/src/components/ui/toast.tsx`

A single `<ToastProvider>` (mounted in `AppProviders`) renders one stacked, bottom-right region; `useToast().toast(text, opts)` pushes from anywhere. Toasts queue, auto-dismiss (4 s), and announce through an `aria-live` region.

- **Feedback rule.** Success / non-blocking notices use a toast. Form-validation errors stay **inline** next to the field (RFC 7807 `detail`), never a toast the user might miss.
- **Test-id contract.** `testId` defaults to `"admin-toast"`, and the toast carries `data-tone` + `data-toast-key`, mirroring the markup every e2e harness selects (`[data-testid="admin-toast"][data-tone][data-toast-key]`). Pass a `tone` (`success` / `error`) and a locale-independent `key`; ScanCancelButton overrides `testId: "scan-cancel-toast"`.
- **Exceptions.** Two surfaces keep a bespoke local toast: the Scan-detail download notice (neutral `data-toast-variant`, not a success / error tone) and the Settings tab's inline `settings-toast` save confirmation. Both have their own tested contracts and do not fit the success / error model.

## Micro-interaction guide

The W11-F polish phase standardised the timing and easing of every interactive transition. Components should pick their motion from the tokens, not hand-roll new values.

| Interaction | Duration | Easing | Property |
|---|---|---|---|
| Button / link hover | 150 ms (`--duration-fast`) | `--ease-out` | `background-color`, `color`, `border-color` |
| Badge tint shift on hover | 150 ms | `--ease-out` | `background-color` |
| Focus ring fade-in | 150 ms | `--ease-out` | `box-shadow`, `outline` |
| Dropdown / popover open | 200 ms (`--duration-base`) | `--ease-out` | `opacity`, `transform: translateY` |
| Drawer slide | 200 ms | `--ease-out` | `transform: translateX` |
| Dialog open | 200 ms | `--ease-out` | `opacity` (backdrop), `transform: scale` (panel) |
| Tab indicator shift | 200 ms | `--ease-out` | `transform: translateX` |
| Page chrome — sidebar collapse | 250 ms (`--duration-slow`) | `--ease-out` | `width` |
| Route change entrance | 250 ms (`--duration-slow`) | `--ease-out` | `opacity` (`<main>` keyed on pathname) |
| Skeleton pulse | 2000 ms loop (`animate-pulse`) | `ease-in-out` | `opacity` |

**Never use the default browser easing.** Always reference `--ease-out` so motion reads as a single continuous language across the product.

**Reduced motion.** A global `@media (prefers-reduced-motion: reduce)` guard in `index.css` collapses every animation and transition above to ~0 (and disables smooth scrolling), so users who request reduced motion get instant state changes — see [Accessibility](#accessibility).

## Accessibility

The portal targets **WCAG 2.1 Level AA**. Three policies make this concrete.

### Contrast — body text 4.5:1, UI 3:1

| Pair | Ratio | Note |
|---|---|---|
| `--foreground` on `--background` | 16.97:1 | Body text. AAA. |
| `--foreground` on `--card` | 17.72:1 | Body text on card. AAA. |
| `--muted-foreground` on `--background` | 4.98:1 | Captions, secondary text. AA. |
| `--muted-foreground` on `--card` | 5.20:1 | Captions on card. AA. |
| `--primary-foreground` on `--primary` | 16.97:1 | Primary button label. AAA. |
| `--destructive-foreground` on `--destructive` | 4.63:1 | Destructive button label. AA. |
| `--ring` on `--background` | 16.97:1 | Focus ring. AAA. |

Decorative borders (`--border` on `--background`, 1.20:1) are **intentionally low-contrast** — they are visual separators, not informative UI elements, and WCAG 1.4.11 exempts them.

### Severity colour accessibility

Severity hex values (`#dc2626` / `#ea580c` / `#ca8a04` / `#2563eb` / `#71717a`) are brand-fixed. Used as **body text** on a light tint they measure as low as 2.5:1, which fails AA. The fix is structural, not chromatic — when the severity tone is used as text, the rendered colour is the severity's `-foreground` token, a deeper shade of the same hue:

| Tone | Tint background | Text colour | Contrast on a card |
|---|---|---|---|
| `critical` | `bg-risk-critical/10` | `text-risk-critical-foreground` (`#b91c1c`) | 5.54:1 |
| `high` | `bg-risk-high/10` | `text-risk-high-foreground` (`#9a3412`) | 6.46:1 |
| `medium` | `bg-risk-medium/10` | `text-risk-medium-foreground` (`#854d0e`) | 6.21:1 |
| `low` | `bg-risk-low/10` | `text-risk-low-foreground` (`#1d4ed8`) | 5.82:1 |
| `info` | `bg-risk-info/10` | `text-risk-info-foreground` (`#52525b`) | 6.85:1 |

Until G0-7 these were spelled as raw Tailwind palette classes (`text-red-700`) with the measurements written in a code comment. They are tokens now, so the same pairing is reused everywhere and the numbers come from a test rather than a comment.

The **dot indicators** (in `SeverityBadge`, chart legends, status pills) continue to use the raw `bg-risk-X` token — colour identity stays recognisable; only text shade is darkened. The reference implementation is `apps/frontend/src/components/ui/badge.tsx`.

One consequence worth stating plainly: `--muted-foreground` clears AA on the page ground by 0.03, so it does **not** clear AA on a risk tint. Secondary text inside a tinted callout takes `text-foreground` and relies on size and weight for hierarchy.

### Colour is not the only signal

Every place severity is shown, colour is paired with one of: a textual label ("Critical"), a Lucide icon (`ShieldAlert`, `TriangleAlert`), or a dot + label combination. The portal must remain usable in greyscale.

### Keyboard navigation

All interactive elements are reachable by `Tab` and operable by `Enter` / `Space`. The portal does not trap focus except inside an open `Dialog` (where the focus-trap is intentional).

- Sidebar links: `Tab` cycles through the visible items.
- Skip link: the first tabstop on every authenticated screen. `sr-only` until focused, then visible; `Enter` moves focus to `<main id="main-content" tabindex="-1">` so the next `Tab` continues inside the content rather than restarting at the top.
- Global bar, in DOM order: menu (below `lg`), brand, team switcher, search trigger, notifications, account menu. All of them are `Tab`-reachable. The account menu holds the profile link, the documentation link, the shortcut sheet, the theme and locale toggles, and sign-out; `Esc` closes it. The team switcher is a `menuitemradio` group, so the current team is announced rather than left to a decorative check mark.
- Table rows: each row that opens a drawer is rendered as a `<button>` or `<a>`; row activation is `Enter`.
- Drawer: `Esc` closes; first tabstop is the close `X`; `Tab` cycles inside the drawer panel while it's open.
- Dialog: same pattern as drawer plus a focus-trap. `Esc` cancels.
- Active filter chips: each chip's `×` is a `<button>`; `Tab`-reachable.
- Combo boxes (`Select`, search): `↑ ↓` to navigate options, `Enter` to commit, `Esc` to dismiss.

### Forms

- Every `<input>` has an associated `<label>` — either visually or via `aria-label`.
- Error messages live in `<p role="alert" aria-live="polite">` next to the field.
- Required fields show a `*` adjacent to the label and `aria-required="true"`.
- Validation runs on blur and on submit — not on every keystroke (which causes screen-reader chatter).

### Live regions

- Toast notifications use `aria-live="polite"`.
- Scan progress bars use `aria-live="polite"` and update their label as the stage changes ("Detecting components" → "Matching CVEs" → "Generating report").
- Long-running CI build-gate output uses `aria-live="polite"` so screen readers announce stage transitions.

## Change history

| Wave | Date | Change |
|---|---|---|
| W11-A | 2026-05-27 | Token redefinition — Vercel base + Linear polish. Primary `#0f172a` → `#18181b`; background `#ffffff` → `#fafafa`; new radius / shadow / motion / focus-ring tokens. |
| W11-B | 2026-05-27 | Foundation re-skin — Button / Input / Select / Card / Badge against new tokens; Project list as the first prototype screen. |
| W11-C | 2026-05-27 | Table / Drawer / Dialog re-skin (PR #244). |
| W11-D | 2026-05-27 | Chart re-skin — Recharts grid / axis / tooltip tokens (PR #245). |
| W11-E | 2026-05-27 | 8 EN + 3 KO before-after PNG comparison (PR #246). |
| W11-F | 2026-05-27 | Micro-interaction polish — hover / focus / motion (PR #247). |
| W11-G | 2026-05-27 | Empty state illustrations (PR #248). |
| W11-H | 2026-05-27 | **A11y sweep + design system docs.** Severity badge text colours darkened to clear WCAG AA on light tints (no token change). This page added. |
| W12-A | 2026-06-11 | **Craft elevation — typography & page-header system.** Added typography primitives (`PageTitle` / `SectionTitle` / `Subtitle` / `Body` / `Caption` / `Eyebrow`) and a shared `PageHeader` (stacked / bar). Unifies the page-title scale (was `text-lg` vs `text-base`) and header chrome (`bg-card` vs `bg-background`) that had drifted across screens. |
| W12-B | 2026-06-11 | **Craft elevation — global toast.** Added a `ToastProvider` + `useToast()` (queue, auto-dismiss, `aria-live`), migrating 11 hand-rolled per-page toasts onto it while preserving the `admin-toast` / `data-toast-key` e2e contract. Scan-detail download notice + Settings inline confirmation kept as documented exceptions. |
| W12-C | 2026-06-11 | **Craft elevation — motion (CSS-only).** Route-change entrance fade (`<main>` keyed on pathname, 250 ms), sidebar collapse aligned to 250 ms, and a global `prefers-reduced-motion` guard. No new dependency (tailwindcss-animate only). Skeleton doc corrected to the real 2000 ms `animate-pulse`. |
| W12-D | 2026-06-12 | **Craft elevation — empty / loading polish.** EmptyState gains a layered icon medallion + optional `illustration` slot; new `TableRowsSkeleton` renders per-column loading cells (replacing single full-width bars) on the Scans and Admin Users tables. |
| W12-E/F | 2026-06-12 | **Craft elevation — guardrails + docs.** Grew `/dev/design-preview` into a living component reference (typography, badges, empty / loading, feedback) and added a "Frontend UI" section to the contributor coding standards. Visual-regression baseline expansion (4 → ~15) is a CI / operator follow-up — correct linux baselines cannot be generated from a darwin dev box. |

The previous "enterprise-style 2015" aesthetic (`#0f172a` navy, pure white canvas, uniform 8 px radius, no shadow, default browser easing) is fully retired by W11.

## See also

- [Architecture](./architecture.md) — backend / frontend / scan pipeline overview.
- [Coding standards](../contributor-guide/coding-standards.md) — formatting, linting, commit conventions.
- The design decisions summarised here are maintained alongside the project's internal planning notes, which are not published with this site. This page is the public source of truth for the token contract.
