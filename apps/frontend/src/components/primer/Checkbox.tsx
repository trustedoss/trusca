// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Checkbox on Primer's Checkbox. It renders a native `input[type=checkbox]`,
 * so roles, test ids and form participation work as before. Pass
 * `indeterminate` for the mixed state instead of setting it through a ref.
 */
import { Checkbox as PrimerCheckbox } from "@primer/react";
import { forwardRef, type ComponentPropsWithoutRef } from "react";

export type CheckboxProps = ComponentPropsWithoutRef<typeof PrimerCheckbox>;

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>((props, ref) => (
  <PrimerCheckbox ref={ref} {...props} />
));
Checkbox.displayName = "Checkbox";
