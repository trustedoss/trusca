import * as ProgressPrimitive from "@radix-ui/react-progress";
import { forwardRef } from "react";

import { cn } from "@/lib/utils";

/**
 * Progress — shadcn/ui standard primitive (PR #9).
 *
 * Built on Radix `react-progress`. The portal uses this for scan progress
 * (CLAUDE.md "디자인 시스템" — long async work shows a progress bar with a
 * stage label). Visual fill uses the primary token; risk-tinted variants
 * are layered by callers that want to communicate failure (e.g. red bar on
 * step="failed").
 */
export const Progress = forwardRef<
  React.ElementRef<typeof ProgressPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof ProgressPrimitive.Root> & {
    indicatorClassName?: string;
  }
>(({ className, indicatorClassName, value, ...props }, ref) => (
  <ProgressPrimitive.Root
    ref={ref}
    className={cn(
      "relative h-2 w-full overflow-hidden rounded-full bg-muted",
      className,
    )}
    {...props}
  >
    <ProgressPrimitive.Indicator
      // W11-F polish — the fill animates between two `translateX` states as
      // the value updates (every WS tick during a scan). Bare `transition-all`
      // landed on Tailwind's default 150 ms linear; we route it through the
      // W11-A `--duration-base` (200 ms) + ease-out-soft so the fill glides
      // forward instead of snapping.
      className={cn(
        "h-full w-full flex-1 bg-primary transition-all duration-base ease-out-soft",
        indicatorClassName,
      )}
      style={{ transform: `translateX(-${100 - (value ?? 0)}%)` }}
    />
  </ProgressPrimitive.Root>
));
Progress.displayName = ProgressPrimitive.Root.displayName;
