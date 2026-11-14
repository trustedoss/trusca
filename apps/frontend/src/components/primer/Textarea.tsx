// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/** Multi-line input on Primer's Textarea; same call shape as the old one. */
import { Textarea as PrimerTextarea } from "@primer/react";
import { forwardRef, type ComponentPropsWithoutRef } from "react";

export type TextareaProps = ComponentPropsWithoutRef<typeof PrimerTextarea>;

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ "aria-invalid": ariaInvalid, ...props }, ref) => (
    <PrimerTextarea
      ref={ref}
      block
      aria-invalid={ariaInvalid}
      validationStatus={ariaInvalid === true || ariaInvalid === "true" ? "error" : undefined}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";
