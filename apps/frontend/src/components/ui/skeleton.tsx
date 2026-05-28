import { forwardRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Skeleton — shadcn/ui standard primitive (PR #9).
 *
 * Loading state for cards, table rows, and progress UI. CLAUDE.md
 * "디자인 시스템" prescribes skeleton placeholders, not spinners, for the
 * top-level loading state.
 *
 * W11-F polish — the placeholder picks up `rounded-sm` (4 px, matching the
 * W11-A chip/input radius) so skeleton rows visually anticipate the badges
 * and inputs that will replace them. The base `animate-pulse` motion is
 * unchanged (Tailwind's 2 s ease-in-out keyframe is already gentle enough
 * for a Linear-style loading rhythm — Vercel deployments uses the same).
 */
export const Skeleton = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("animate-pulse rounded-sm bg-muted", className)}
      {...props}
    />
  ),
);
Skeleton.displayName = "Skeleton";
