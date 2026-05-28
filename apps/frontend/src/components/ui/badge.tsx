import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Badge — shadcn/ui standard primitive (PR #9).
 *
 * Risk-tinted variants pair a status word (Critical / High / …) with the
 * design-system color token so color is never the only signal. The numeric
 * dots / pills sit alongside an icon or text label in higher-level components.
 */
const badgeVariants = cva(
  // W11-B polish — `rounded-sm` aligns badges with the W11-A radius hierarchy
  // (sm = 4 px for chips/badges, md = 6 px for buttons/cards). Hover colour
  // transitions also pick up the 150 ms ease-out-soft motion tokens.
  "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 text-xs font-medium transition-colors duration-fast ease-out-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 whitespace-nowrap",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-primary text-primary-foreground hover:bg-primary/80",
        secondary:
          "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
        outline: "text-foreground",
        destructive:
          "border-transparent bg-destructive text-destructive-foreground hover:bg-destructive/80",
        // BUG-001 (a11y): the previous `text-muted-foreground` on `bg-muted`
        // measured 4.34:1 — below WCAG AA (4.5:1) — and axe flagged the
        // "new"/"suppressed" status badges. Scoping a darker subtle slate to
        // THIS variant only (not the global `--muted-foreground` token) keeps
        // the badge visually subtle while clearing AA (6.9:1 light / 9.8:1
        // dark) without regressing every other muted-foreground surface.
        muted:
          "border-transparent bg-muted text-slate-600 hover:bg-muted/80 dark:text-slate-300",
      },
      tone: {
        none: "",
        // Risk tones — W11-H (a11y sweep).
        //
        // Background uses the `--risk-X` token at low alpha so the chip reads
        // as a coloured tint while preserving brand semantics. The text colour
        // is intentionally a darker shade of the SAME hue family from the
        // Tailwind palette so the rendered text/tint contrast clears
        // WCAG AA 4.5:1 — `text-risk-X` (the raw token) measured 2.5 ~ 4.5:1
        // on the blended tint and was failing AA. Measurements (alpha-blended
        // against #ffffff card surface):
        //
        //   critical  text-red-700      → 5.54:1
        //   high      text-orange-800   → 6.47:1
        //   medium    text-yellow-800   → 5.91:1
        //   low       text-blue-700     → 5.83:1
        //   info      text-slate-600    → 6.41:1
        //
        // The dot indicators (SeverityBadge / DependencyScopeBadge / chart
        // legends) still use `bg-risk-X` raw so the brand colour stays
        // recognisable — only the text shade is darkened. Token values in
        // `index.css` are NOT changed (W11 prohibition: "Severity 색 변경 0").
        critical: "border-transparent bg-risk-critical/10 text-red-700",
        high: "border-transparent bg-risk-high/10 text-orange-800",
        medium: "border-transparent bg-risk-medium/15 text-yellow-800",
        low: "border-transparent bg-risk-low/10 text-blue-700",
        info: "border-transparent bg-risk-info/15 text-slate-600",
        success: "border-transparent bg-emerald-100 text-emerald-700",
      },
    },
    defaultVariants: {
      variant: "outline",
      tone: "none",
    },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant, tone, ...props }, ref) => (
    <span
      ref={ref}
      className={cn(badgeVariants({ variant, tone }), className)}
      {...props}
    />
  ),
);
Badge.displayName = "Badge";

export { badgeVariants };
