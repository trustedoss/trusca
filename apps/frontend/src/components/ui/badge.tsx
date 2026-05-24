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
  "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 whitespace-nowrap",
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
        // Risk tones lean on the design tokens declared in index.css.
        // Backgrounds use opacity so the text remains legible at light + dark.
        critical: "border-transparent bg-risk-critical/10 text-risk-critical",
        high: "border-transparent bg-risk-high/10 text-risk-high",
        medium: "border-transparent bg-risk-medium/15 text-risk-medium",
        low: "border-transparent bg-risk-low/10 text-risk-low",
        info: "border-transparent bg-risk-info/15 text-risk-info",
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
