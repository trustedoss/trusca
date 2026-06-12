---
id: design-system
title: Design system
description: TRUSCA design system — tokens (colour, spacing, radius, shadow, motion, typography), component conventions, micro-interactions, accessibility, and the W13 Google AI Studio re-skin.
sidebar_label: Design system
sidebar_position: 10
---

# Design system

The portal frontend follows a single, light-mode design system in the **Google AI Studio** tone (W13, 2026-06-12): white canvas, Google-blue primary, tonal secondary, pill buttons, flat bordered cards. The typography hierarchy, motion, and focus polish introduced with W11 (Linear-inspired) are retained. Dark mode is deferred to v2.5+.

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
2. **Pack data without feeling cramped.** Compact 40 px table rows; 224 px sidebar; 48 px header; 16 / 20 / 24 px card padding scale.
3. **Read as a modern product (Google AI Studio tone).** White canvas (`#ffffff`) with `#dadce0` hairline borders; Google-blue primary (`#0b57d0`) with a tonal light-blue secondary; pill buttons; flat cards that separate via border + tone rather than shadow; semibold headings; visible focus rings. Fonts stay Inter + JetBrains Mono (Google Sans is proprietary).
4. **Move only as much as needed.** Motion is short and ease-out — 150 ms for hover / focus, 200 ms for drawer slide, 250 ms for page-level chrome. No bounce, no fade-in delays.

### W13 (2026-06-12) — Google AI Studio re-skin

W13 replaced the W11 Vercel + Linear skin with the Google AI Studio light tone, adopted after a dev-toggle prototype review (PR #394). Structural decisions (sidebar nav, 40 px row, drawer-for-detail, risk semantics) and the W11 polish layer (typography, motion, focus ring) carry over unchanged. What changed:

| Surface | W11 (before) | W13 (after) |
|---|---|---|
| Primary CTA | `#18181b` warm near-black | `#0b57d0` Google blue |
| Secondary | muted grey tint | `#d3e3fd` tonal light blue |
| Page background | `#fafafa` off-white canvas | `#ffffff` white canvas |
| Card surface | white, lifts via shadow-sm | white, flat — separates via border + tone |
| Hover / ghost tint | muted grey | `#f0f4f9` faint blue-grey |
| Border | `#e5e5ea` | `#dadce0` AIS hairline |
| Button shape | rounded-md (6 px) | pill (`rounded-full`) |
| Radius scale | sm 4 / md 6 / lg 8 / xl 12 | sm 6 / md 8 / lg 10 / xl 14 |
| Shadow | sm (card) / md / lg | sm = none (flat); md / lg = Google elevation-1/-2 |
| Focus ring | near-black | `#0b57d0` blue (matches primary) |
| `--risk-low` | `#2563eb` blue-600 | `#0f766e` teal-700 (no longer collides with the blue primary) |

Risk severity colours for Critical / High / Medium / Info are **unchanged** — the domain semantics are fixed. Low is the single W13 exception (user decision): the old blue read like a CTA / link next to the new Google-blue primary. Where a severity hex fails WCAG AA as body text on a light tint, the badge text shade is darkened within the same hue family (see [Severity colour accessibility](#severity-colour-accessibility) below).

<details>
<summary>W11 (2026-05-27) — the previous Vercel + Linear refresh</summary>

W11 replaced the original "BD-style 2015" aesthetic (navy `#0f172a`, uniform 8 px radius, default easing) with a Vercel light base + Linear polish: warm near-black `#18181b` primary, `#fafafa` canvas, radius / shadow / motion token hierarchy, semibold headings, visible focus rings, and the drawer + page dual detail surface. The polish layer survives in W13; the colour skin does not.

</details>

## Colour tokens

All colour decisions reference the CSS custom properties declared in `index.css`. Components should never reference hex values directly — use the Tailwind utility (`bg-background`, `text-foreground`, `bg-risk-critical/10`) or the CSS variable.

### Neutral palette (Google AI Studio light)

| Token | Hex | HSL | Use |
|---|---|---|---|
| `--background` | `#ffffff` | `0 0% 100%` | Page canvas — plain white (AIS). |
| `--card` | `#ffffff` | `0 0% 100%` | Cards, popovers, drawer body, tooltip — flush with the canvas, separated by border + tone. |
| `--foreground` | `#1f1f1f` | `0 0% 12%` | Body text — Google grey-900. |
| `--muted` | `#f8f9fa` | `210 17% 98%` | Subtle fills — table headers, sidebar tint, placeholder backgrounds, disabled inputs. |
| `--muted-foreground` | `#5f6368` | `213 5% 39%` | Secondary text, captions, table column headers — Google grey-700. |
| `--border` | `#dadce0` | `220 9% 87%` | Hairline borders — the AIS standard. Decorative separator only, never the sole means of identifying a UI region. |
| `--input` | `#dadce0` | `220 9% 87%` | Input outline. |
| `--primary` | `#0b57d0` | `217 90% 43%` | Primary CTA — Google blue, "the important action on the page". |
| `--primary-foreground` | `#ffffff` | `0 0% 100%` | Text on primary. |
| `--secondary` | `#d3e3fd` | `217 91% 91%` | Tonal button fill (Google "tonal" pattern). |
| `--secondary-foreground` | `#041e49` | `217 90% 15%` | Text on tonal fill. |
| `--accent` | `#f0f4f9` | `213 43% 96%` | Hover rows, ghost-button hover — faint blue-grey tint. |
| `--destructive` | `#d93025` | `4 71% 50%` | Destructive CTA — Google red, near `--risk-critical` so destructive buttons share severity-badge visual language. |
| `--destructive-foreground` | `#ffffff` | `0 0% 100%` | Text on destructive. |
| `--ring` | `#0b57d0` | `217 90% 43%` | Focus ring. Matches primary so the outline reads as "the same action this is". |

### Risk severity (domain semantics — fixed)

| Token | Hex | Use |
|---|---|---|
| `--risk-critical` | `#dc2626` | Critical CVE, forbidden licence, build-blocking finding. |
| `--risk-high` | `#ea580c` | High-severity CVE, conditional licence at risk. |
| `--risk-medium` | `#ca8a04` | Medium CVE, conditional licence awaiting review. |
| `--risk-low` | `#0f766e` | Low CVE, informational status. Teal-700 since W13 — moved out of the blue family so Low badges don't read like the blue primary CTA. |
| `--risk-info` | `#71717a` | Neutral informational state. |

The severity hex values are stable across releases (the W13 Low move is the documented exception). They appear in:

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

Different affordances use different radii so depth reads at a glance. W13 moved the whole scale one step rounder.

| Token | Value | Affordance |
|---|---|---|
| `--radius-sm` | 6 px | Small inputs, badges, chips. |
| `--radius` | 8 px | **Default** — cards, inputs, table chrome. |
| `--radius-lg` | 10 px | Drawer, large surfaces. |
| `--radius-xl` | 14 px | Modals, dialogs. |

The Tailwind config derives `rounded-sm`, `rounded-md`, `rounded-lg`, `rounded-xl` from these tokens via `calc()`. **Buttons are pills** (`rounded-full` in `button.tsx`) and deliberately not part of this scale — raising `--radius` to a pill value would full-round cards and dialogs through the `calc()` derivations.

## Shadow scale

AIS keeps in-flow surfaces flat — cards and buttons separate via border + tone. Shadows are reserved for floating surfaces and follow the Google elevation recipes.

| Token | Value | Use |
|---|---|---|
| `--shadow-sm` | `0 0 0 0 rgb(0 0 0 / 0)` | Intentionally a zero-alpha no-op (flat cards / buttons). Kept a valid box-shadow value so `var(--shadow-sm)` consumers don't break, and so a future token change can re-enable elevation in one place. |
| `--shadow-md` | `0 1px 2px 0 rgb(60 64 67 / 0.3), 0 1px 3px 1px rgb(60 64 67 / 0.15)` | Dropdown, popover, tooltip (Google elevation-1). |
| `--shadow-lg` | `0 1px 3px 0 rgb(60 64 67 / 0.3), 0 4px 8px 3px rgb(60 64 67 / 0.15)` | Drawer, dialog (Google elevation-2). |

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

- **Pill shape** (`rounded-full`, W13) on every variant and size — the AIS button silhouette. Inputs keep the token radius; only buttons (and sidebar nav items) are pills.
- Default variant uses `bg-primary text-primary-foreground` — solid Google blue.
- `secondary` variant is the Google **tonal** button — light-blue fill (`--secondary`), deep-blue text.
- `outline` variant uses `border-input bg-background` — for secondary actions.
- `ghost` variant uses no background, hover tint only (`--accent`) — for nav items and toolbar actions.
- `destructive` uses `bg-destructive` — Google red near `--risk-critical`, reserved for irreversible actions (delete, revoke, reject).
- Hover and focus transitions use `transition-colors duration-fast ease-out` (150 ms).
- All variants include the focus ring.

### Input / Select / Checkbox

- Border colour `--input`, focus ring `--ring`.
- Disabled state uses `bg-muted text-muted-foreground`.
- Error state uses `border-destructive` plus an `aria-live="polite"` message under the field.

### Card

- White surface (`bg-card`) flush on the white canvas — separation comes from the `--border` hairline and the `--muted` tone, not elevation (AIS flat-card pattern; `shadow-sm` resolves to a no-op).
- `rounded-md` (8 px) by default; `rounded-lg` (10 px) for primary content cards.
- `shadow-md` only on floating surfaces (popover / dropdown), never on in-flow cards.

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
| `--foreground` on `--background` / `--card` | 16.48:1 | Body text. AAA. |
| `--muted-foreground` on `--background` / `--card` | 6.05:1 | Captions, secondary text. AA. |
| `--muted-foreground` on `--muted` | 5.74:1 | Captions on tinted fills. AA. |
| `--primary-foreground` on `--primary` | 6.39:1 | Primary button label. AA. |
| `--secondary-foreground` on `--secondary` | 12.57:1 | Tonal button label. AAA. |
| `--destructive-foreground` on `--destructive` | 4.77:1 | Destructive button label. AA. |
| `--ring` on `--background` | 6.39:1 | Focus ring. AA (UI ≥ 3:1). |

Decorative borders (`--border` on `--background`, 1.37:1) are **intentionally low-contrast** — they are visual separators, not informative UI elements, and WCAG 1.4.11 exempts them.

### Severity colour accessibility

Severity hex values (`#dc2626` / `#ea580c` / `#ca8a04` / `#0f766e` / `#71717a`) are brand-fixed. Used as **body text** on a light tint some would measure as low as 2.5:1, which fails AA. The fix is structural, not chromatic — when the severity tone is used as text, the rendered text colour uses a deeper shade from the same Tailwind hue family:

| Tone | Tint background | Text colour | Contrast |
|---|---|---|---|
| `critical` | `bg-risk-critical/10` | `text-red-700` (`#b91c1c`) | 5.54:1 |
| `high` | `bg-risk-high/10` | `text-orange-800` (`#9a3412`) | 6.47:1 |
| `medium` | `bg-risk-medium/15` | `text-yellow-800` (`#854d0e`) | 5.91:1 |
| `low` | `bg-risk-low/10` | `text-teal-800` (`#115e59`) | 6.59:1 |
| `info` | `bg-risk-info/15` | `text-slate-600` (`#52525b`) | 6.41:1 |

The raw Low token itself (teal-700 `#0f766e`) also clears AA where it's used directly as text (5.47:1 on white, 4.76:1 on its own 10 % tint) — slightly better than the blue-600 it replaced.

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
| W12-D | 2026-06-12 | **Craft elevation — empty / loading polish.** EmptyState gains a layered icon medallion + optional `illustration` slot; new `TableRowsSkeleton` renders per-column loading cells (replacing single full-width bars) on the Scans and Admin Users tables. |
| W12-E/F | 2026-06-12 | **Craft elevation — guardrails + docs.** Grew `/dev/design-preview` into a living component reference (typography, badges, empty / loading, feedback) and added a "Frontend UI" section to the contributor coding standards. Visual-regression baseline expansion (4 → ~15) is a CI / operator follow-up — correct linux baselines cannot be generated from a darwin dev box. |
| W13 | 2026-06-12 | **Google AI Studio re-skin (TRUSCA).** Token promotion from the PR #394 dev prototype after user review: white canvas, `#0b57d0` blue primary, `#d3e3fd` tonal secondary, `#dadce0` borders, radius scale +2 px, flat cards (shadow-sm → no-op, md/lg → Google elevation recipes), pill buttons (`rounded-full` in `button.tsx` + sidebar nav), `--risk-low` blue-600 → teal-700 to separate Low badges from the blue primary. Layout density, motion, typography, and the other severity colours unchanged. Visual-regression baselines require a linux refresh after merge. |

The W11 Vercel + Linear skin is retired by W13; the earlier "BD-style 2015" aesthetic was retired by W11.

## See also

- [Architecture](./architecture.md) — backend / frontend / scan pipeline overview.
- [Coding standards](../contributor-guide/coding-standards.md) — formatting, linting, commit conventions.
- [`CLAUDE.md`](https://github.com/trustedoss/trusca/blob/main/CLAUDE.md) — top-level project rules. The "디자인 시스템 (v2)" section summarises this page.
- W11 source-of-truth plan — `docs/ux/design-philosophy-evolution-plan-2026-05-27.md` (in-repo).
