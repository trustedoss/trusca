// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * The ⌘K palette's building blocks.
 *
 * Primer has no command palette, so this keeps `cmdk` for what it does well
 * (filtering, ranking, arrow-key selection, the combobox ARIA wiring) and
 * puts it inside Primer's Dialog for the modal behaviour. Only the look is
 * ours: tokens from index.css, Octicons for the glyphs.
 */
import { SearchIcon } from "@primer/octicons-react";
import { Dialog } from "@primer/react";
import { Command as CommandPrimitive } from "cmdk";
import {
  forwardRef,
  type ComponentPropsWithoutRef,
  type ElementRef,
  type HTMLAttributes,
} from "react";

import { cn } from "@/lib/utils";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Accessible name of the dialog; not drawn. */
  label: string;
  children: React.ReactNode;
}

export function CommandPalette({
  open,
  onOpenChange,
  label,
  children,
}: CommandPaletteProps) {
  if (!open) return null;
  return (
    <Dialog
      title={label}
      width="large"
      position="center"
      align="top"
      onClose={() => onOpenChange(false)}
      className="command-palette"
      renderHeader={({ dialogLabelId }) => (
        // The input is the visible header; the title is for assistive tech.
        <Dialog.Header className="sr-only">
          <h2 id={dialogLabelId}>{label}</h2>
        </Dialog.Header>
      )}
    >
      <CommandPrimitive
        className={cn(
          "flex w-full flex-col overflow-hidden text-foreground",
          "[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground",
        )}
      >
        {children}
      </CommandPrimitive>
    </Dialog>
  );
}

export const PaletteInput = forwardRef<
  ElementRef<typeof CommandPrimitive.Input>,
  ComponentPropsWithoutRef<typeof CommandPrimitive.Input>
>(({ className, ...props }, ref) => (
  <div className="flex items-center border-b px-3" cmdk-input-wrapper="">
    <SearchIcon size={16} className="mr-2 shrink-0 text-muted-foreground" />
    <CommandPrimitive.Input
      ref={ref}
      className={cn(
        "flex h-12 w-full bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground",
        className,
      )}
      {...props}
    />
  </div>
));
PaletteInput.displayName = "PaletteInput";

export const PaletteList = forwardRef<
  ElementRef<typeof CommandPrimitive.List>,
  ComponentPropsWithoutRef<typeof CommandPrimitive.List>
>(({ className, ...props }, ref) => (
  <CommandPrimitive.List
    ref={ref}
    className={cn("max-h-[400px] overflow-y-auto overflow-x-hidden", className)}
    {...props}
  />
));
PaletteList.displayName = "PaletteList";

export const PaletteEmpty = forwardRef<
  ElementRef<typeof CommandPrimitive.Empty>,
  ComponentPropsWithoutRef<typeof CommandPrimitive.Empty>
>((props, ref) => (
  <CommandPrimitive.Empty
    ref={ref}
    className="py-6 text-center text-sm text-muted-foreground"
    {...props}
  />
));
PaletteEmpty.displayName = "PaletteEmpty";

export const PaletteGroup = forwardRef<
  ElementRef<typeof CommandPrimitive.Group>,
  ComponentPropsWithoutRef<typeof CommandPrimitive.Group>
>(({ className, ...props }, ref) => (
  <CommandPrimitive.Group
    ref={ref}
    className={cn("overflow-hidden p-2 text-foreground", className)}
    {...props}
  />
));
PaletteGroup.displayName = "PaletteGroup";

export const PaletteItem = forwardRef<
  ElementRef<typeof CommandPrimitive.Item>,
  ComponentPropsWithoutRef<typeof CommandPrimitive.Item>
>(({ className, ...props }, ref) => (
  <CommandPrimitive.Item
    ref={ref}
    className={cn(
      "relative flex cursor-pointer select-none items-center gap-2 rounded-md px-2 py-2 text-sm outline-none",
      "aria-selected:bg-accent aria-selected:text-accent-foreground data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50",
      className,
    )}
    {...props}
  />
));
PaletteItem.displayName = "PaletteItem";

export function PaletteShortcut({
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn("ml-auto text-xs tracking-widest text-muted-foreground", className)}
      {...props}
    />
  );
}
