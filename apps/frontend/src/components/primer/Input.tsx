// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Text input on Primer's TextInput, keeping the shadcn-era call shape
 * (`<Input {...register("name")} aria-invalid=... />`) so forms move over
 * without rewrites. `aria-invalid="true"` also paints the error state.
 */
import { TextInput } from "@primer/react";
import { forwardRef, type ComponentPropsWithoutRef } from "react";

export type InputProps = Omit<ComponentPropsWithoutRef<typeof TextInput>, "size"> & {
  size?: "small" | "medium" | "large";
};

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ "aria-invalid": ariaInvalid, size = "medium", ...props }, ref) => (
    <TextInput
      ref={ref}
      block
      size={size}
      aria-invalid={ariaInvalid}
      validationStatus={ariaInvalid === true || ariaInvalid === "true" ? "error" : undefined}
      {...props}
    />
  ),
);
Input.displayName = "Input";
