// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { forwardRef, type HTMLAttributes } from "react";

import { Badge } from "@/components/primer/badge";
import { cn } from "@/lib/utils";

/**
 * StatusPill, the one shape for a state (scan status, approval status, account
 * status): a dot and a label on a subtle filled pill. The dot takes the label
 * colour, so the tone is carried by the word, the dot and the fill together.
 *
 * Amber (`warning`) is attention, not the Medium severity grade; severities
 * keep using `Badge tone="medium"` through SeverityBadge.
 */
export type StatusPillTone =
  | "success"
  | "progress"
  | "warning"
  | "danger"
  | "neutral";

export interface StatusPillProps extends HTMLAttributes<HTMLSpanElement> {
  tone: StatusPillTone;
}

export const StatusPill = forwardRef<HTMLSpanElement, StatusPillProps>(
  ({ tone, className, children, ...props }, ref) => (
    <Badge
      ref={ref}
      tone={tone}
      data-tone={tone}
      className={cn("gap-1.5", className)}
      {...props}
    >
      <span
        aria-hidden
        className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-current"
      />
      <span>{children}</span>
    </Badge>
  ),
);
StatusPill.displayName = "StatusPill";
