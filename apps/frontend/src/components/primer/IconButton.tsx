// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { IconButton as PrimerIconButton } from "@primer/react";
import type { IconProps } from "@primer/octicons-react";
import {
  forwardRef,
  type ButtonHTMLAttributes,
  type FunctionComponent,
  type PropsWithChildren,
} from "react";

/**
 * A square button that shows only an icon.
 *
 * The visible label is gone, so the accessible name is required and Primer
 * shows the same text as a tooltip on hover and focus: a sighted mouse user
 * and a screen reader user read the same word. Keep it for places where
 * width is tight and the icon is familiar (columns, download, view switch);
 * a primary action should keep its text.
 */
export interface IconButtonProps
  extends Omit<
    ButtonHTMLAttributes<HTMLButtonElement>,
    "aria-label" | "aria-labelledby" | "children"
  > {
  /** Octicon component, rendered at Primer's 16 px. */
  icon: FunctionComponent<PropsWithChildren<IconProps>>;
  /** Translated accessible name, also shown as the tooltip. */
  "aria-label": string;
  size?: "small" | "medium";
  variant?: "default" | "invisible";
  "data-testid"?: string;
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ icon, size = "medium", variant = "default", ...rest }, ref) => (
    <PrimerIconButton
      ref={ref}
      icon={icon}
      size={size}
      variant={variant}
      {...rest}
    />
  ),
);
IconButton.displayName = "IconButton";
