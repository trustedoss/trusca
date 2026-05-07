import * as TabsPrimitive from "@radix-ui/react-tabs";
import {
  forwardRef,
  type ComponentPropsWithoutRef,
  type ElementRef,
} from "react";

import { cn } from "@/lib/utils";

/**
 * Tabs — shadcn/ui primitive built on `@radix-ui/react-tabs` (chore PR #5).
 *
 * Replaces the hand-rolled stand-in introduced in PR #10. The radix primitive
 * gives us roving keyboard focus, `data-state="active|inactive"`, and proper
 * `role="tab|tablist|tabpanel"` semantics out of the box, matching the
 * canonical shadcn/ui Tabs component
 * (https://ui.shadcn.com/docs/components/tabs).
 *
 * The exported API (`Tabs`, `TabsList`, `TabsTrigger`, `TabsContent`) is
 * identical to the previous stand-in so existing call sites and Playwright
 * harness selectors (`role="tab"`, `data-state`, `data-testid`) continue to
 * work unchanged.
 */

export const Tabs = TabsPrimitive.Root;

export const TabsList = forwardRef<
  ElementRef<typeof TabsPrimitive.List>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn(
      "inline-flex items-center gap-1 border-b bg-background px-2",
      className,
    )}
    {...props}
  />
));
TabsList.displayName = TabsPrimitive.List.displayName;

export const TabsTrigger = forwardRef<
  ElementRef<typeof TabsPrimitive.Trigger>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      "inline-flex h-9 items-center whitespace-nowrap rounded-t-md border-b-2 border-transparent px-3 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      "disabled:pointer-events-none disabled:opacity-50",
      "data-[state=active]:border-primary data-[state=active]:text-foreground",
      className,
    )}
    {...props}
  />
));
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;

export const TabsContent = forwardRef<
  ElementRef<typeof TabsPrimitive.Content>,
  ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn("flex flex-col", className)}
    {...props}
  />
));
TabsContent.displayName = TabsPrimitive.Content.displayName;
