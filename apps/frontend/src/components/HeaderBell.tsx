// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * HeaderBell — Phase 6 chore A2.
 *
 * Renders the notification bell in the AppShell header. Behaviour:
 *   - Polls `GET /v1/notifications/unread-count` every 60 s while the tab
 *     is visible. TanStack Query's `refetchIntervalInBackground: false`
 *     pauses the timer when `document.visibilityState !== "visible"`,
 *     mirroring the `useScanWebSocket` pattern of "no work while hidden".
 *   - Click navigates to `/notifications` (the inbox + preferences page).
 *   - Badge displays the count, capped at "99+" once the count exceeds
 *     99. Hidden entirely when the count is 0 so the header stays calm.
 *
 * Design note: we deliberately do NOT add a sidebar nav entry for the
 * notification center. The bell is the entry point — keeping the
 * sidebar focused on top-level domains (Projects / Scans / Approvals /
 * Integrations) matches the commercial-SCA-style enterprise SCA aesthetic
 * pinned in CLAUDE.md "디자인 시스템".
 */
import { Bell } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { useUnreadCount } from "@/features/notifications/useNotifications";
import { formatBadge } from "@/lib/badgeCount";
import { cn } from "@/lib/utils";

export interface HeaderBellProps {
  /** Rendered on the dark global bar — use the topbar foreground scale. */
  onInk?: boolean;
  /** Disable the underlying query (tests use this to keep timers quiet). */
  enabled?: boolean;
}

export function HeaderBell({ enabled = true, onInk = false }: HeaderBellProps) {
  const { t } = useTranslation("common");
  const navigate = useNavigate();
  const unreadQuery = useUnreadCount({ enabled });
  const count = unreadQuery.data?.count ?? 0;
  const badge = formatBadge(count);
  const showBadge = badge !== "";

  return (
    <Button
      type="button"
      variant={onInk ? "ghost" : "outline"}
      size="sm"
      onClick={() => navigate("/notifications")}
      data-testid="header-bell"
      data-unread-count={count}
      aria-label={
        showBadge
          ? t("nav.bell.aria_with_count", { count })
          : t("nav.bell.aria")
      }
      className={cn(
        "relative",
        // Sitting on the ink global bar, the default ghost hover (a light
        // accent) would flash near-white. The topbar scale keeps it dark.
        onInk &&
          "text-topbar-foreground hover:bg-topbar-accent hover:text-topbar-foreground",
      )}
    >
      <Bell className="h-4 w-4" aria-hidden />
      {showBadge ? (
        <span
          data-testid="header-bell-badge"
          className={cn(
            "absolute -right-1 -top-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold leading-none",
            // Use the Critical risk token to draw the eye — pairs the
            // color signal with the count text per the a11y rule.
            "bg-risk-critical text-white",
          )}
        >
          {badge}
        </span>
      ) : null}
    </Button>
  );
}
