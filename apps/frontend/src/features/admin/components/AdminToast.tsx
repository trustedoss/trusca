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
  /**
   * Phase 4 PR #13. Stable, locale-independent identifier for the toast.
   * Surfaces as ``data-toast-key`` so e2e tests can assert on which
   * invariant produced the toast without depending on translated copy.
   *
   * Successful mutations: ``role_updated`` | ``deactivated`` | ``activated``
   * | ``password_reset_sent`` | ``created`` | ``updated`` | ``deleted`` |
   * ``member_added`` | ``member_removed``.
   *
   * Errors: the snake_case extension from the backend Problem payload —
   * ``last_super_admin_protected`` | ``cannot_modify_self`` |
   * ``last_team_admin_protected`` | ``team_has_active_scans`` |
   * ``invalid_role_assignment`` | ``slug_conflict`` | ``unknown``.
   */
  key?: string;
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
      data-toast-key={message.key ?? ""}
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
