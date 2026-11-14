// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/** A pulsing placeholder block. Size it with className (h-*, w-*). */
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("animate-pulse rounded-sm bg-muted", className)} {...props} />;
}
