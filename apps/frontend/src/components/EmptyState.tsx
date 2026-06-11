/**
 * EmptyState — W11-G shared empty-state primitive.
 *
 * Centralises the "no data yet" affordance the portal shows in eight
 * canonical zero-state surfaces (Projects, Scans, Vulnerabilities,
 * Licenses, Obligations, Reports, Policies, Notifications). Before W11-G
 * each surface hand-rolled its own Card/border/dashed/heading combo, so
 * the visual language drifted (font weight, gap, icon presence,
 * background tone). Funnelling all of them through this one component
 * gives the system a single "calm, friendly, on-brand" empty-state look
 * that pairs a muted lucide icon with the existing i18n copy — no new
 * illustration libraries, no new SVG assets.
 *
 * Visual contract (matches `docs/ux/design-philosophy-evolution-plan-2026-05-27.md` §4):
 *   - Outer column, items + content centered, generous vertical padding.
 *   - Icon nested in a layered medallion (W12-D): two soft concentric muted
 *     rings behind a raised white inner disc, so the icon reads as a designed
 *     "subject" rather than a flat glyph. Icon is `text-muted-foreground` so
 *     colour stays calm. A caller may pass `illustration` to swap the
 *     medallion for a richer inline SVG.
 *   - Title is one notch heavier than body copy (`font-semibold`).
 *   - Description capped at `max-w-md` so long sentences don't span the
 *     whole table viewport.
 *   - Optional action slot for the rare CTA (e.g. "Register project").
 *
 * Accessibility: the root carries `role="status"` so screen readers can
 * announce "no projects yet" without us having to plumb aria-live in
 * each call site. Decorative icon is `aria-hidden` since the title
 * already carries the meaning.
 */
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  /** Lucide icon (or any small inline node) rendered inside the medallion. */
  icon: ReactNode;
  /**
   * Optional richer inline illustration rendered in place of the icon
   * medallion (e.g. a domain SVG). Inline only — no new asset/library, so the
   * SCA self-scan surface stays unchanged. When omitted, the layered icon
   * medallion is used.
   */
  illustration?: ReactNode;
  /** Primary, already-translated headline. */
  title: string;
  /** Optional supporting copy (already translated). */
  description?: string;
  /** Optional CTA button/link cluster rendered below the description. */
  action?: ReactNode;
  /** Optional class overrides for the outer wrapper. */
  className?: string;
  /** Optional test id forwarded to the root for harness reachability. */
  "data-testid"?: string;
}

export function EmptyState({
  icon,
  illustration,
  title,
  description,
  action,
  className,
  "data-testid": dataTestId,
}: EmptyStateProps) {
  return (
    <div
      role="status"
      data-testid={dataTestId}
      className={cn(
        "flex flex-col items-center justify-center gap-3 py-12 px-6 text-center",
        className,
      )}
    >
      {illustration ? (
        <div aria-hidden="true">{illustration}</div>
      ) : (
        /* Layered medallion (W12-D) — two soft concentric muted rings behind a
           raised white inner disc holding the icon. Reads as a designed
           "subject" rather than a flat glyph, using tokens only (muted /
           border / shadow-sm). */
        <div
          aria-hidden="true"
          className="relative flex h-20 w-20 items-center justify-center"
        >
          <div className="absolute inset-0 rounded-full bg-muted/40" />
          <div className="absolute inset-[10px] rounded-full bg-muted/70 ring-1 ring-border/60" />
          <div className="relative flex h-12 w-12 items-center justify-center rounded-full bg-background text-muted-foreground shadow-sm ring-1 ring-border [&_svg]:h-6 [&_svg]:w-6">
            {icon}
          </div>
        </div>
      )}
      <p className="text-base font-semibold text-foreground">{title}</p>
      {description ? (
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
