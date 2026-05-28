import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Tiny inline pill that hangs off a distribution card's title to call out
 * **which axis** the chart groups by — components vs findings. Two cards on
 * the same project (Overview's "Vulnerability severity" + the Vulnerabilities
 * tab's same-titled card) measure different things, and during user testing
 * users compared the counts and assumed they were broken.
 *
 * Pure presentational — locale-agnostic, no business logic. Pass the localized
 * label (e.g. `t("overview.severity_card.axis_components")`) as the child.
 */
export interface AxisPillProps {
  children: ReactNode;
  className?: string;
}

export function AxisPill({ children, className }: AxisPillProps) {
  return (
    <span
      data-testid="distribution-axis-pill"
      className={cn(
        "inline-flex shrink-0 items-center rounded-full border border-border bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground",
        className,
      )}
    >
      {children}
    </span>
  );
}
