import { forwardRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Skeleton — shadcn/ui standard primitive (PR #9).
 *
 * Loading state for cards, table rows, and progress UI. CLAUDE.md
 * "디자인 시스템" prescribes skeleton placeholders, not spinners, for the
 * top-level loading state.
 */
export const Skeleton = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  ),
);
Skeleton.displayName = "Skeleton";
