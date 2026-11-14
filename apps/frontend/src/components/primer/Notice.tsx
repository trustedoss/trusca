// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * An inline message box: an error, a confirmation, a note.
 *
 * Replaces the shadcn Alert. Colour is never the only signal, so every tone
 * carries an icon, and the tints come from the status tokens (they have dark
 * values; raw palette classes did not). Errors use role="alert" so they are
 * announced at once; the rest use role="status".
 */
import {
  AlertIcon,
  CheckCircleIcon,
  InfoIcon,
  type Icon,
} from "@primer/octicons-react";
import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

export type NoticeTone = "error" | "success" | "info" | "warning";

const TONE: Record<NoticeTone, { icon: Icon; box: string }> = {
  error: {
    icon: AlertIcon,
    box: "border-status-danger-border bg-status-danger-subtle text-status-danger-foreground",
  },
  success: {
    icon: CheckCircleIcon,
    box: "border-status-success-border bg-status-success-subtle text-status-success-foreground",
  },
  info: {
    icon: InfoIcon,
    box: "border-status-info-border bg-status-info-subtle text-status-info-foreground",
  },
  warning: {
    icon: AlertIcon,
    box: "border-status-warning-border bg-status-warning-subtle text-status-warning-foreground",
  },
};

interface NoticeProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  tone?: NoticeTone;
  children: ReactNode;
}

export function Notice({ tone = "info", className, children, ...rest }: NoticeProps) {
  const { icon: ToneIcon, box } = TONE[tone];
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn("flex items-start gap-2 rounded-md border px-3 py-2.5 text-sm", box, className)}
      {...rest}
    >
      <ToneIcon size={16} className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
