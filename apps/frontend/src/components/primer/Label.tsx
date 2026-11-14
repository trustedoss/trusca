// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { forwardRef, type LabelHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** A plain label. Primer styles fields through FormControl, which this app does not use. */
export const Label = forwardRef<HTMLLabelElement, LabelHTMLAttributes<HTMLLabelElement>>(
  ({ className, ...props }, ref) => (
    <label ref={ref} className={cn("text-sm font-medium leading-none", className)} {...props} />
  ),
);
Label.displayName = "Label";
