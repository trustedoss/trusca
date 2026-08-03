/**
 * Badge `muted` variant — WCAG AA contrast guard (BUG-001).
 *
 * The "new" / "suppressed" vulnerability status badges use `variant="muted"`.
 * axe-core's `color-contrast` rule (serious) flagged them at 4.34:1 — below
 * WCAG AA (4.5:1) — because the previous `text-muted-foreground` on `bg-muted`
 * was too low-contrast.
 *
 * axe's color-contrast rule needs a real rendering engine (it does not run in
 * jsdom), so this test guards the fix two ways without a browser:
 *   1. The `muted` variant wires the accessible token (`text-slate-600` /
 *      `dark:text-slate-300`) and NOT the failing `text-muted-foreground`.
 *   2. The resolved colors clear 4.5:1 against the muted background in BOTH
 *      light and dark mode (computed from the design tokens here).
 *
 * The full axe `color-contrast` assertion lives in the Playwright e2e (which
 * renders in a real browser) — see `tests/e2e/*`.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Badge, badgeVariants } from "@/components/ui/badge";
import { VulnerabilityStatusBadge } from "@/features/projects/components/VulnerabilityStatusBadge";

// --- WCAG contrast helpers (sRGB relative luminance) ---------------------

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [
    parseInt(h.slice(0, 2), 16) / 255,
    parseInt(h.slice(2, 4), 16) / 255,
    parseInt(h.slice(4, 6), 16) / 255,
  ];
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const sN = s / 100;
  const lN = l / 100;
  const c = (1 - Math.abs(2 * lN - 1)) * sN;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = lN - c / 2;
  let r = 0;
  let g = 0;
  let b = 0;
  if (h < 60) [r, g, b] = [c, x, 0];
  else if (h < 120) [r, g, b] = [x, c, 0];
  else if (h < 180) [r, g, b] = [0, c, x];
  else if (h < 240) [r, g, b] = [0, x, c];
  else if (h < 300) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  return [r + m, g + m, b + m];
}

function relLuminance([r, g, b]: [number, number, number]): number {
  const f = (c: number) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(
  fg: [number, number, number],
  bg: [number, number, number],
): number {
  const l1 = relLuminance(fg);
  const l2 = relLuminance(bg);
  const hi = Math.max(l1, l2);
  const lo = Math.min(l1, l2);
  return (hi + 0.05) / (lo + 0.05);
}

// Design tokens (src/index.css): --muted light = 210 40% 96.1%,
// dark = 217.2 32.6% 17.5%. Tailwind slate-600 = #475569, slate-300 = #cbd5e1.
const MUTED_LIGHT = hslToRgb(210, 40, 96.1);
const MUTED_DARK = hslToRgb(217.2, 32.6, 17.5);
const SLATE_600 = hexToRgb("#475569");
const SLATE_300 = hexToRgb("#cbd5e1");

describe("Badge muted variant contrast (BUG-001)", () => {
  it("wires the accessible slate token, not the failing muted-foreground", () => {
    const classes = badgeVariants({ variant: "muted" });
    expect(classes).toContain("text-slate-600");
    expect(classes).toContain("dark:text-slate-300");
    expect(classes).not.toContain("text-muted-foreground");
  });

  it("clears WCAG AA (>= 4.5:1) in light mode", () => {
    expect(contrast(SLATE_600, MUTED_LIGHT)).toBeGreaterThanOrEqual(4.5);
  });

  it("clears WCAG AA (>= 4.5:1) in dark mode", () => {
    expect(contrast(SLATE_300, MUTED_DARK)).toBeGreaterThanOrEqual(4.5);
  });

  it("documents the regression baseline: the old muted-foreground failed AA", () => {
    const oldFg = hslToRgb(215.4, 16.3, 46.9); // --muted-foreground
    expect(contrast(oldFg, MUTED_LIGHT)).toBeLessThan(4.5);
  });

  it("the new/suppressed status badges render with the muted variant", () => {
    // These are exactly the badges the axe selector flagged
    // (`[data-testid="vulnerability-status-badge-new"] > span`).
    render(
      <>
        <VulnerabilityStatusBadge status="new" />
        <VulnerabilityStatusBadge status="suppressed" />
      </>,
    );
    for (const status of ["new", "suppressed"] as const) {
      const badge = screen.getByTestId(`vulnerability-status-badge-${status}`);
      expect(badge.className).toContain("text-slate-600");
    }
  });

  it("a plain Badge with variant=muted carries the accessible class", () => {
    render(
      <Badge variant="muted" data-testid="plain-muted">
        muted
      </Badge>,
    );
    expect(screen.getByTestId("plain-muted").className).toContain(
      "text-slate-600",
    );
  });
});
