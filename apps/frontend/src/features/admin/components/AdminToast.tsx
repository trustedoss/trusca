/**
 * Lightweight toast surface for admin mutations — Phase 4 PR #13.
 *
 * The portal does not yet have a global toast provider (shadcn `toast`
 * primitive is not in the tree). Instead each admin page renders a fixed
 * `<div>` at the bottom-right that displays the most recent message. The
 * tone (`success` / `error`) maps to the existing `Alert` variants.
 *
 * This is intentionally local — when the wider portal grows a toast
 * provider we can swap this for that without touching call sites.
 */
import { useEffect } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { cn } from "@/lib/utils";

export type AdminToastTone = "success" | "error";

export interface AdminToastMessage {
  id: number;
  text: string;
  tone: AdminToastTone;
}

interface AdminToastProps {
  message: AdminToastMessage | null;
  onDismiss: () => void;
  /** Auto-dismiss after this many milliseconds. Default 4000. */
  ttlMs?: number;
}

export function AdminToast({ message, onDismiss, ttlMs = 4000 }: AdminToastProps) {
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(onDismiss, ttlMs);
    return () => clearTimeout(timer);
  }, [message, onDismiss, ttlMs]);

  if (!message) return null;
  return (
    <div
      className="fixed bottom-4 right-4 z-50 max-w-sm"
      data-testid="admin-toast"
      data-tone={message.tone}
    >
      <Alert
        variant={message.tone === "error" ? "destructive" : "default"}
        className={cn(
          "shadow-lg",
          message.tone === "success" &&
            "border-emerald-200 bg-emerald-50 text-emerald-900",
        )}
        role="status"
        aria-live="polite"
      >
        <AlertDescription>{message.text}</AlertDescription>
      </Alert>
    </div>
  );
}
