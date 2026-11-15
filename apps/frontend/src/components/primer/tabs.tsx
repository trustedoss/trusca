// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import * as TabsPrimitive from "@radix-ui/react-tabs";
import {
  forwardRef,
  type ComponentPropsWithoutRef,
  type ElementRef,
} from "react";

import { cn } from "@/lib/utils";

/**
 * Tabs — shadcn/ui primitive built on `@radix-ui/react-tabs`.
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
      // G0-5 — `max-w-full flex-wrap`. An `inline-flex` strip is sized by its
      // content, so the project-detail page's 8 tabs insisted on 767 px and
      // pushed the whole page sideways on a 390 px phone: the narrow-viewport
      // gate counted the page shell and this strip as two of its frozen
      // spills, on both project-detail baselines.
      //
      // Wrapping rather than `overflow-x-auto`: a scroller here would clip the
      // triggers' focus ring (`ring-offset-2` draws outside the box) and hide
      // tabs behind an affordance a touch user has to discover. `max-w-full`
      // is what lets the wrap happen at all — without it the strip keeps
      // sizing to max-content and never reaches its wrap point. Above the
      // phone band there is more than 767 px, so nothing wraps and the
      // desktop strip is unchanged.
      "inline-flex max-w-full flex-wrap items-center gap-1 border-b bg-background px-2",
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
      // W11-B polish — hover/focus colour transition lifted to W11-A 150 ms
      // ease-out-soft so tab interactions feel uniform with button/dropdown.
      "inline-flex h-9 items-center whitespace-nowrap rounded-t-md border-b-2 border-transparent px-3 text-sm font-medium text-muted-foreground transition-colors duration-fast ease-out-soft hover:text-foreground",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      "disabled:pointer-events-none disabled:opacity-50",
      // W14 — the active tab is marked by the brand accent. The label stays
      // ink, so the underline is a second signal rather than the only one.
      "data-[state=active]:border-brand data-[state=active]:text-foreground",
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
