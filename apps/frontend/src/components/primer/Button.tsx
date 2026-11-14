// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * The app's button, on Primer's Button.
 *
 * Screens already speak the shadcn vocabulary (variant default/outline/ghost/
 * destructive/secondary/link, size default/sm/lg/icon, `asChild`), so this
 * keeps those names and maps them, and a screen moves over by changing the
 * import. Mapping: default -> primary, outline and secondary -> Primer's
 * default (outlined), ghost -> invisible, destructive -> danger, link -> link.
 *
 * `asChild` renders the one child element (a router Link, an anchor) with the
 * button's look, passing the child's own props through, as Radix's Slot did.
 * `size="icon"` is a square button for an icon child; give it an aria-label.
 */
import { Button as PrimerButtonTyped } from "@primer/react";
import {
  Children,
  forwardRef,
  isValidElement,
  type ComponentPropsWithoutRef,
  type ElementType,
  type ReactElement,
} from "react";

import { cn } from "@/lib/utils";

// Primer types `as` per element; this wrapper forwards whatever element the
// caller's child is, so the polymorphic props are loosened once, here.
const PrimerButton = PrimerButtonTyped as unknown as ElementType;

type Variant = "default" | "destructive" | "outline" | "secondary" | "ghost" | "link";
type Size = "default" | "sm" | "lg" | "icon";

const VARIANT = {
  default: "primary",
  destructive: "danger",
  outline: "default",
  secondary: "default",
  ghost: "invisible",
  link: "link",
} as const;

const SIZE = { default: "medium", sm: "small", lg: "large", icon: "medium" } as const;

export interface ButtonProps
  extends Omit<ComponentPropsWithoutRef<typeof PrimerButtonTyped>, "variant" | "size" | "as"> {
  variant?: Variant;
  size?: Size;
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "default", size = "default", asChild = false, className, children, ...rest },
  ref,
) {
  const common = {
    variant: VARIANT[variant],
    size: SIZE[size],
    className: cn("whitespace-nowrap", size === "icon" && "primer-btn-icon", className),
  };
  if (asChild) {
    const child = Children.only(children);
    if (isValidElement(child)) {
      const el = child as ReactElement<Record<string, unknown> & { children?: React.ReactNode }>;
      const { children: inner, className: childClass, ...childProps } = el.props;
      return (
        <PrimerButton
          ref={ref}
          as={el.type as ElementType}
          {...common}
          {...rest}
          {...childProps}
          className={cn(common.className, childClass as string | undefined)}
        >
          {inner}
        </PrimerButton>
      );
    }
  }
  return (
    <PrimerButton ref={ref} {...common} {...rest}>
      {children}
    </PrimerButton>
  );
});
