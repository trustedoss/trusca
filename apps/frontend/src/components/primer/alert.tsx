// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * An inline message box with a title and description slots.
 *
 * Same call shape as the shadcn Alert it replaces. The tones come from the
 * status tokens (they have dark values), so a tone reads the same in both
 * themes; `destructive` is the error tone and stays the default for errors.
 * Any icon passed as the first child is placed in the left gutter.
 */
const alertVariants = cva(
  "relative w-full rounded-md border px-4 py-3 text-sm [&>svg+div]:translate-y-[-3px] [&>svg]:absolute [&>svg]:left-4 [&>svg]:top-4 [&>svg~*]:pl-7",
  {
    variants: {
      variant: {
        default: "bg-background text-foreground [&>svg]:text-foreground",
        destructive:
          "border-status-danger-border bg-status-danger-subtle text-status-danger-foreground [&>svg]:text-status-danger-foreground",
        warning:
          "border-status-warning-border bg-status-warning-subtle text-status-warning-foreground [&>svg]:text-status-warning-foreground",
        info: "border-status-info-border bg-status-info-subtle text-status-info-foreground [&>svg]:text-status-info-foreground",
        success:
          "border-status-success-border bg-status-success-subtle text-status-success-foreground [&>svg]:text-status-success-foreground",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export const Alert = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement> & VariantProps<typeof alertVariants>
>(({ className, variant, ...props }, ref) => (
  <div
    ref={ref}
    role="alert"
    className={cn(alertVariants({ variant }), className)}
    {...props}
  />
));
Alert.displayName = "Alert";

export const AlertTitle = forwardRef<
  HTMLParagraphElement,
  HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h5
    ref={ref}
    className={cn("mb-1 font-medium leading-none tracking-tight", className)}
    {...props}
  />
));
AlertTitle.displayName = "AlertTitle";

export const AlertDescription = forwardRef<
  HTMLParagraphElement,
  HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("text-sm [&_p]:leading-relaxed", className)}
    {...props}
  />
));
AlertDescription.displayName = "AlertDescription";
