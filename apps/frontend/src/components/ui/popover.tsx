// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import * as PopoverPrimitive from "@radix-ui/react-popover";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";

import { cn } from "@/lib/utils";

/**
 * Popover - shadcn/ui standard primitive.
 *
 * Built on Radix Popover so we get focus management, outside-click/ESC
 * dismissal, and `aria-expanded`/`aria-controls` wiring on the trigger for
 * free. Distinct from `Dialog` (centered modal, focus-trapped) and `Sheet`
 * (right-side drawer): a popover is anchored to its trigger and does not
 * block interaction with the rest of the page.
 *
 * First consumer is the group-picker combobox on the project-creation form
 * (group-hierarchy Phase 6): a `Popover` anchoring the existing `Command`
 * primitives (`command.tsx`) so a search box + result list can attach to a
 * plain trigger button instead of the full-screen `CommandDialog`.
 */
export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;
export const PopoverAnchor = PopoverPrimitive.Anchor;

export const PopoverContent = forwardRef<
  ElementRef<typeof PopoverPrimitive.Content>,
  ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>
>(({ className, align = "start", sideOffset = 4, ...props }, ref) => (
  <PopoverPrimitive.Portal>
    <PopoverPrimitive.Content
      ref={ref}
      align={align}
      sideOffset={sideOffset}
      className={cn(
        // Mirrors DropdownMenuContent's motion/elevation tokens (dropdown-menu.tsx)
        // so the popover family reads consistently: 150ms ease-out-soft, shadow-md.
        "z-50 w-72 rounded-md border bg-popover p-0 text-popover-foreground shadow-md outline-none",
        "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
        "data-[state=open]:duration-fast data-[state=closed]:duration-fast ease-out-soft",
        className,
      )}
      {...props}
    />
  </PopoverPrimitive.Portal>
));
PopoverContent.displayName = PopoverPrimitive.Content.displayName;
