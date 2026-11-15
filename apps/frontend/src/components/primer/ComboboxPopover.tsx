// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * A searchable picker that opens under its trigger.
 *
 * Primer's AnchoredOverlay supplies the placement, outside-click and Escape
 * handling and the focus move; `cmdk` supplies the listbox semantics and arrow
 * keys (the same split as the ⌘K palette). The caller owns the list, so server
 * side search keeps working: filtering is switched off.
 */
import { ChevronDownIcon, SearchIcon } from "@primer/octicons-react";
import { AnchoredOverlay, Button } from "@primer/react";
import { Command as CommandPrimitive } from "cmdk";
import type { ReactNode } from "react";

import { PaletteList } from "@/components/primer/CommandPalette";
import { cn } from "@/lib/utils";

interface ComboboxPopoverProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  triggerId?: string;
  triggerTestId?: string;
  triggerLabel: ReactNode;
  searchValue: string;
  onSearchChange: (value: string) => void;
  searchPlaceholder: string;
  searchTestId?: string;
  searchAriaLabel?: string;
  /** Extra attributes for the trigger, such as data-* hooks. */
  triggerProps?: Record<string, string>;
  /** Fixed trigger width class; the trigger fills its row when omitted. */
  triggerClassName?: string;
  listTestId?: string;
  children: ReactNode;
}

export function ComboboxPopover({
  open,
  onOpenChange,
  triggerId,
  triggerTestId,
  triggerLabel,
  searchValue,
  onSearchChange,
  searchPlaceholder,
  searchTestId,
  searchAriaLabel,
  triggerProps,
  triggerClassName,
  listTestId,
  children,
}: ComboboxPopoverProps) {
  return (
    <AnchoredOverlay
      open={open}
      onOpen={() => onOpenChange(true)}
      onClose={() => onOpenChange(false)}
      width="medium"
      // cmdk owns the arrow keys inside the list; the overlay's own focus
      // zone would take them first.
      focusZoneSettings={{ disabled: true }}
      renderAnchor={(anchorProps) => (
        <Button
          {...anchorProps}
          id={triggerId}
          type="button"
          role="combobox"
          aria-expanded={open}
          block={!triggerClassName}
          className={triggerClassName}
          {...triggerProps}
          trailingVisual={ChevronDownIcon}
          data-testid={triggerTestId}
          alignContent="start"
        >
          <span className="truncate">{triggerLabel}</span>
        </Button>
      )}
    >
      <CommandPrimitive shouldFilter={false} className="flex w-full flex-col text-foreground">
        <div className="flex items-center border-b px-3" cmdk-input-wrapper="">
          <SearchIcon size={16} className="mr-2 shrink-0 text-muted-foreground" />
          <CommandPrimitive.Input
            value={searchValue}
            onValueChange={onSearchChange}
            placeholder={searchPlaceholder}
            data-testid={searchTestId}
            aria-label={searchAriaLabel}
            className={cn(
              "flex h-10 w-full bg-transparent py-2 text-sm outline-none placeholder:text-muted-foreground",
            )}
          />
        </div>
        <PaletteList data-testid={listTestId} className="max-h-[300px] p-1">
          {children}
        </PaletteList>
      </CommandPrimitive>
    </AnchoredOverlay>
  );
}
