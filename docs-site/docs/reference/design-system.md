---
id: design-system
title: Design system
description: TrustedOSS Portal design system — tokens (colour, spacing, radius, shadow, motion, typography), component conventions, micro-interactions, accessibility, and the W11 visual-identity refresh.
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

TrustedOSS Portal is a **risk-first, information-dense, modern enterprise SCA** tool. The visual identity must:

1. **Communicate severity at a glance.** Risk colours (Critical / High / Medium / Low / Info) appear next to a textual label and an icon or dot — colour is never the sole signal.
2. **Pack data without feeling cramped.** Compact 40 px table rows; 224 px sidebar; 48 px header; 16 / 20 / 24 px card padding scale.
3. **Read as a modern enterprise product.** Warm near-black (`#18181b`) instead of navy (`#0f172a`); off-white canvas (`#fafafa`) so cards lift visually; subtle shadows; semibold headings; visible focus rings.
4. **Move only as much as needed.** Motion is short and ease-out — 150 ms for hover / focus, 200 ms for drawer slide, 250 ms for page-level chrome. No bounce, no fade-in delays.

### W11 (2026-05-27) — visual refresh

The W11 milestone replaced the previous "BD-style 2015" aesthetic with the current Vercel+Linear blend. The structural decisions (sidebar nav, 40 px row, drawer-for-detail, risk semantics) carry over unchanged. What changed:

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
| `--muted-foreground` | `#71717a` | `240 4% 46%` | Secondary text, captions, table column headers. |
| `--border` | `#e5e5ea` | `240 5% 91%` | Hairline borders. Decorative separator only — never the sole means of identifying a UI region. |
| `--input` | `#e5e5ea` | `240 5% 91%` | Input outline. |
| `--primary` | `#18181b` | `240 6% 10%` | Primary CTA — "the important action on the page". |
| `--primary-foreground` | `#fafafa` | `0 0% 98%` | Text on primary. |
| `--destructive` | `#dc2626` | `0 72% 51%` | Destructive CTA. Aligned with `--risk-critical` so destructive buttons share severity-badge visual language. |
| `--destructive-foreground` | `#fafafa` | `0 0% 98%` | Text on destructive. |
| `--ring` | `#18181b` | `240 6% 10%` | Focus ring. Matches primary so the outline reads as "the same action this is". |

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
- `bg-risk-X/N` tints on badges and dot indicators.
- Border accents (`border-risk-high/40` on buttons and alerts).

When severity colour is used as **body text** (a coloured word inside a badge or alert), use a deeper shade from the same Tailwind hue family — see [Severity colour accessibility](#severity-colour-accessibility).

## Spacing

| Token | Value | Use |
|---|---|---|
| `--layout-sidebar` | 224 px | Expanded sidebar width (default). |
| `--layout-sidebar-collapsed` | 64 px | Icon-only rail width when the user collapses the sidebar (≥`lg`). |
| `--layout-header` | 48 px | Top header height. |
| `--table-row` | 40 px | Compact table row height. |

**Sidebar behaviour.** The left sidebar is **user-collapsible and viewport-responsive**:

- **≥ `lg` (1024 px):** fixed sidebar. A toggle at the bottom of the rail collapses it from 224 px to a 64 px icon-only rail; collapsed labels move to `aria-label` + native hover tooltip. The choice persists across reloads (`uiStore` → `localStorage` key `trustedoss-ui`). Width animates over `--duration-base`.
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
- Optional small SVG illustration on top (W11-G), title (semibold), description (muted), single primary CTA.
- Used for: empty list, empty search result, empty drawer tab, first-time onboarding card.

### Badge

`apps/frontend/src/components/ui/badge.tsx`

Risk-tinted variants pair a status word with the design-system colour. Background uses `bg-risk-X/10` (or `/15` for medium / info) so the chip reads as a coloured tint. Text uses a deeper shade from the same hue family so the rendered contrast clears WCAG AA 4.5:1 — see [Severity colour accessibility](#severity-colour-accessibility).

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
| `--muted-foreground` on `--background` | 4.63:1 | Captions, secondary text. AA. |
| `--muted-foreground` on `--card` | 4.83:1 | Captions on card. AA. |
| `--primary-foreground` on `--primary` | 16.97:1 | Primary button label. AAA. |
| `--destructive-foreground` on `--destructive` | 4.63:1 | Destructive button label. AA. |
| `--ring` on `--background` | 16.97:1 | Focus ring. AAA. |

Decorative borders (`--border` on `--background`, 1.20:1) are **intentionally low-contrast** — they are visual separators, not informative UI elements, and WCAG 1.4.11 exempts them.

### Severity colour accessibility

Severity hex values (`#dc2626` / `#ea580c` / `#ca8a04` / `#2563eb` / `#71717a`) are brand-fixed. Used as **body text** on a light tint they would measure as low as 2.5:1, which fails AA. The fix is structural, not chromatic — when the severity tone is used as text, the rendered text colour uses a deeper shade from the same Tailwind hue family:

| Tone | Tint background | Text colour | Contrast |
|---|---|---|---|
| `critical` | `bg-risk-critical/10` | `text-red-700` (`#b91c1c`) | 5.54:1 |
| `high` | `bg-risk-high/10` | `text-orange-800` (`#9a3412`) | 6.47:1 |
| `medium` | `bg-risk-medium/15` | `text-yellow-800` (`#854d0e`) | 5.91:1 |
| `low` | `bg-risk-low/10` | `text-blue-700` (`#1d4ed8`) | 5.83:1 |
| `info` | `bg-risk-info/15` | `text-slate-600` (`#52525b`) | 6.41:1 |

The **dot indicators** (in `SeverityBadge`, chart legends, status pills) continue to use the raw `bg-risk-X` token — colour identity stays recognisable; only text shade is darkened. The reference implementation is `apps/frontend/src/components/ui/badge.tsx` (W11-H).

### Colour is not the only signal

Every place severity is shown, colour is paired with one of: a textual label ("Critical"), a Lucide icon (`ShieldAlert`, `TriangleAlert`), or a dot + label combination. The portal must remain usable in greyscale.

### Keyboard navigation

All interactive elements are reachable by `Tab` and operable by `Enter` / `Space`. The portal does not trap focus except inside an open `Dialog` (where the focus-trap is intentional).

- Sidebar links: `Tab` cycles through the visible items.
- Top header: avatar dropdown, locale toggle, sign-out — `Tab`-reachable.
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

The previous "BD-style 2015" aesthetic (`#0f172a` navy, pure white canvas, uniform 8 px radius, no shadow, default browser easing) is fully retired by W11.

## See also

- [Architecture](./architecture.md) — backend / frontend / scan pipeline overview.
- [Coding standards](../contributor-guide/coding-standards.md) — formatting, linting, commit conventions.
- [`CLAUDE.md`](https://github.com/trustedoss/trustedoss-portal/blob/main/CLAUDE.md) — top-level project rules. The "디자인 시스템 (v2)" section summarises this page.
- W11 source-of-truth plan — `docs/ux/design-philosophy-evolution-plan-2026-05-27.md` (in-repo).
