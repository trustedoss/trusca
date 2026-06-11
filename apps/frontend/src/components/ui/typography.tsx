import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Typography — W12-A shared text primitives.
 *
 * Before W12-A every page hand-rolled its text styles with raw Tailwind
 * utilities, so the same *role* drifted across screens: the page title was
 * `text-lg` on Scans / Admin but `text-base` on Dashboard / Project list, and
 * subtitles were `text-sm` here, `text-xs` there. This module captures the
 * scale documented in `docs-site/docs/reference/design-system.md` §Typography
 * as a single source so a "page title" is one thing everywhere.
 *
 * Scale (matches the design-system table — Inter, semibold headings, never
 * bold; body 14 px with the −0.005em tightening applied globally on <body>):
 *
 *   pageTitle    18 px / semibold / tracking-tight   — the one H1 per page
 *   sectionTitle 16 px / semibold / tracking-tight   — section / card titles
 *   subtitle     14 px / regular  / muted            — sits under a page title
 *   body         14 px / regular                     — default body copy
 *   bodyMuted    14 px / regular  / muted            — secondary body copy
 *   caption      12 px / regular  / muted            — dense meta / timestamps
 *   eyebrow      12 px / medium   / uppercase / muted — overlines, col headers
 *
 * Heading elements (h1–h4) already inherit `font-semibold tracking-tight` from
 * the base layer in `index.css`; the explicit classes here keep the variant
 * self-describing and correct even when rendered on a non-heading element.
 */
export const textVariants = cva("", {
  variants: {
    variant: {
      pageTitle: "text-lg font-semibold tracking-tight text-foreground",
      sectionTitle: "text-base font-semibold tracking-tight text-foreground",
      subtitle: "text-sm text-muted-foreground",
      body: "text-sm text-foreground",
      bodyMuted: "text-sm text-muted-foreground",
      caption: "text-xs text-muted-foreground",
      eyebrow:
        "text-xs font-medium uppercase tracking-wide text-muted-foreground",
    },
  },
  defaultVariants: {
    variant: "body",
  },
});

export type TextVariant = NonNullable<
  VariantProps<typeof textVariants>["variant"]
>;

/** Page title — the single H1 per route. Use inside `PageHeader`. */
export const PageTitle = forwardRef<
  HTMLHeadingElement,
  HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h1
    ref={ref}
    className={cn(textVariants({ variant: "pageTitle" }), className)}
    {...props}
  />
));
PageTitle.displayName = "PageTitle";

/** Section / sub-area heading (H2). Card titles use the shadcn `CardTitle`. */
export const SectionTitle = forwardRef<
  HTMLHeadingElement,
  HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h2
    ref={ref}
    className={cn(textVariants({ variant: "sectionTitle" }), className)}
    {...props}
  />
));
SectionTitle.displayName = "SectionTitle";

/** Muted supporting line shown beneath a `PageTitle`. */
export const Subtitle = forwardRef<
  HTMLParagraphElement,
  HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p
    ref={ref}
    className={cn(textVariants({ variant: "subtitle" }), className)}
    {...props}
  />
));
Subtitle.displayName = "Subtitle";

/** Default body copy. */
export const Body = forwardRef<
  HTMLParagraphElement,
  HTMLAttributes<HTMLParagraphElement> & { muted?: boolean }
>(({ className, muted = false, ...props }, ref) => (
  <p
    ref={ref}
    className={cn(
      textVariants({ variant: muted ? "bodyMuted" : "body" }),
      className,
    )}
    {...props}
  />
));
Body.displayName = "Body";

/** Dense meta text (timestamps, counts). */
export const Caption = forwardRef<
  HTMLSpanElement,
  HTMLAttributes<HTMLSpanElement>
>(({ className, ...props }, ref) => (
  <span
    ref={ref}
    className={cn(textVariants({ variant: "caption" }), className)}
    {...props}
  />
));
Caption.displayName = "Caption";

/** Uppercase overline / column-group label. */
export const Eyebrow = forwardRef<
  HTMLSpanElement,
  HTMLAttributes<HTMLSpanElement>
>(({ className, ...props }, ref) => (
  <span
    ref={ref}
    className={cn(textVariants({ variant: "eyebrow" }), className)}
    {...props}
  />
));
Eyebrow.displayName = "Eyebrow";
