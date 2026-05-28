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
 *   - Icon nested in a 64px muted circle so the icon reads as a "subject"
 *     rather than a free-floating glyph. Icon itself is 32px, rendered
 *     in `text-muted-foreground` so colour stays calm.
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
  /** Lucide icon (or any small inline node) rendered inside the muted circle. */
  icon: ReactNode;
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
      <div
        aria-hidden="true"
        className="flex h-16 w-16 items-center justify-center rounded-full bg-muted text-muted-foreground [&_svg]:h-8 [&_svg]:w-8"
      >
        {icon}
      </div>
      <p className="text-base font-semibold text-foreground">{title}</p>
      {description ? (
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
