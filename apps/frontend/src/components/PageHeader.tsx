import type { HTMLAttributes, ReactNode } from "react";

import { PageTitle, Subtitle } from "@/components/ui/typography";
import { cn } from "@/lib/utils";

/** Allow `data-*` attributes (e.g. `data-testid`) on forwarded prop bags. */
type DataAttributes = { [key: `data-${string}`]: string | number | boolean };

/**
 * PageHeader — W12-A canonical page-header pattern.
 *
 * Before W12-A every route hand-rolled its own `<header>` + `<h1>` + optional
 * `<p>`, and they drifted: title `text-lg` vs `text-base`, subtitle `text-sm`
 * vs `text-xs`, chrome `bg-card` vs `bg-background`. This component funnels all
 * page headers through one place so the page-title typography and header chrome
 * are identical everywhere, while still supporting the two legitimate
 * archetypes the portal uses:
 *
 *   variant="stacked" (default) — taller header (`py-4`) carrying a title and a
 *     muted subtitle. For pages that need an explanatory line (Scans, Admin).
 *
 *   variant="bar" — slim 48 px row (`var(--layout-header)`), title on the left
 *     and an optional right slot (actions or meta), no subtitle. For dense
 *     pages whose purpose is self-evident (Dashboard, Project list).
 *
 * Chrome is unified to `bg-background` + `border-b`: the off-white canvas with
 * a hairline divider, so the white surfaces (cards, tables) below read as
 * raised — consistent with the token philosophy in `index.css`.
 *
 * The `actions` slot is a generic right-aligned area: it holds buttons
 * (Project list "Register") or meta text (Dashboard "last updated"); the caller
 * keeps full control of its markup and `data-testid`, so existing harness
 * selectors are preserved.
 */
export interface PageHeaderProps {
  /** Already-translated page title (rendered as the single H1). */
  title: ReactNode;
  /** Optional muted subtitle (stacked variant only). */
  description?: ReactNode;
  /**
   * Optional block rendered under the description (stacked variant only) —
   * e.g. a "last updated 2m ago" timestamp line with its own test id. Kept
   * separate from `description` so the caller controls its element + styling
   * without nesting block content inside the subtitle `<p>`.
   */
  meta?: ReactNode;
  /** Optional right-aligned slot: action buttons or meta text. */
  actions?: ReactNode;
  /** Layout archetype — see component doc. */
  variant?: "stacked" | "bar";
  /** Class overrides for the `<header>` element. */
  className?: string;
  /** Test id forwarded to the `<header>` root. */
  "data-testid"?: string;
  /** Extra props (e.g. `data-testid`) forwarded to the H1 title element. */
  titleProps?: HTMLAttributes<HTMLHeadingElement> & DataAttributes;
  /** Extra props forwarded to the subtitle element. */
  descriptionProps?: HTMLAttributes<HTMLParagraphElement> & DataAttributes;
}

export function PageHeader({
  title,
  description,
  meta,
  actions,
  variant = "stacked",
  className,
  "data-testid": testId,
  titleProps,
  descriptionProps,
}: PageHeaderProps) {
  if (variant === "bar") {
    return (
      <header
        data-testid={testId}
        className={cn(
          "flex shrink-0 items-center justify-between border-b bg-background px-6",
          className,
        )}
        style={{ height: "var(--layout-header)" }}
      >
        <PageTitle {...titleProps}>{title}</PageTitle>
        {actions ? (
          <div className="flex items-center gap-2">{actions}</div>
        ) : null}
      </header>
    );
  }

  return (
    <header
      data-testid={testId}
      className={cn("border-b bg-background px-6 py-4", className)}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <PageTitle {...titleProps}>{title}</PageTitle>
          {description ? (
            <Subtitle className="mt-1" {...descriptionProps}>
              {description}
            </Subtitle>
          ) : null}
          {meta ? <div className="mt-1">{meta}</div> : null}
        </div>
        {actions ? (
          <div className="flex shrink-0 items-center gap-2">{actions}</div>
        ) : null}
      </div>
    </header>
  );
}
