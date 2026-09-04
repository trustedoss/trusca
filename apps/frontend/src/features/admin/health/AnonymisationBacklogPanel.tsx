// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * AnonymisationBacklogPanel: approved erasures nobody has run yet (ER32).
 *
 * Unlike its neighbours this panel does not report on a subsystem. It reports
 * on an obligation the deployment has taken on and not yet discharged.
 *
 * Approval happens here, in the product. The erasure runs as an operator
 * command on a server, because the database role that may reach inside
 * `audit_logs` is deliberately withheld from the application. In between,
 * every screen looks healthy while somebody waits, and erasure requests
 * usually carry a statutory deadline. That gap is the whole reason for this
 * panel: an empty state is the normal case, and any row is work somebody has
 * to go and do.
 *
 * Presentation follows from that. The panel is neutral when empty rather than
 * green, because nothing has been verified. There is simply nothing owed.
 * A waiting row turns amber and, past a week, red: the severity tracks how
 * long a person has been waiting, not how the software is behaving.
 *
 * Only ids are shown. Rendering the subject's email on the screen that tracks
 * its removal would put it back on a screen, in a screenshot, and in whatever
 * support ticket the screenshot lands in.
 *
 * e2e anchors: root `data-testid="anonymisation-backlog-panel"` +
 * `data-status`, each row `data-testid="anonymisation-backlog-row"` with a raw
 * `data-waiting-days` so Playwright asserts wire values, not formatted text.
 */
import { AlertCircle, CheckCircle2, Clock, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { type AwaitingExecutionItem } from "@/features/admin/health/api/adminAnonymisationApi";
import { useAdminAnonymisation } from "@/features/admin/health/api/useAdminAnonymisation";
import { adminErrorMessageKey } from "@/features/admin/lib/adminErrorMessage";
import { formatAbsoluteTime } from "@/lib/absoluteTime";
import { cn } from "@/lib/utils";

/**
 * When a waiting request stops being routine and starts being late.
 *
 * Seven days matches the window a request has to be approved in. Past it, a
 * request has now spent longer waiting for an operator than it was ever
 * allowed to spend waiting for a decision, which is the point at which the
 * delay is the process failing rather than the process running.
 */
export const OVERDUE_DAYS = 7;

export type PanelStatus = "clear" | "waiting" | "overdue";

export function panelStatus(items: AwaitingExecutionItem[]): PanelStatus {
  if (items.length === 0) return "clear";
  return items.some((item) => item.waiting_days >= OVERDUE_DAYS)
    ? "overdue"
    : "waiting";
}

function statusVisuals(status: PanelStatus): {
  icon: typeof CheckCircle2;
  badge: string;
} {
  if (status === "clear") {
    // Muted, not green. Nothing was checked and found well; there is simply
    // nothing outstanding, and a success colour here would train the eye to
    // read this panel as reassurance rather than as a worklist.
    return { icon: CheckCircle2, badge: "border-border bg-muted text-slate-600" };
  }
  if (status === "waiting") {
    return {
      icon: Clock,
      badge:
        "border-status-warning-border bg-status-warning-subtle text-status-warning-foreground",
    };
  }
  return {
    icon: ShieldAlert,
    badge:
      "border-status-danger-border bg-status-danger-subtle text-status-danger-foreground",
  };
}

export function AnonymisationBacklogPanel() {
  const { t, i18n } = useTranslation("admin");
  const { data, isLoading, isError, error } = useAdminAnonymisation();

  if (isLoading) {
    return (
      <section
        className="rounded-lg border bg-card p-4 shadow-sm"
        data-testid="anonymisation-backlog-panel"
        data-status="loading"
      >
        <Skeleton className="h-5 w-56" />
        <Skeleton className="mt-3 h-16 w-full" />
      </section>
    );
  }

  if (isError) {
    return (
      <section
        className="rounded-lg border bg-card p-4 shadow-sm"
        data-testid="anonymisation-backlog-panel"
        data-status="error"
      >
        <h2 className="text-sm font-semibold">
          {t("admin.anonymisation.title")}
        </h2>
        <Alert variant="destructive" className="mt-3">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{t(adminErrorMessageKey(error))}</AlertDescription>
        </Alert>
      </section>
    );
  }

  const items = data?.items ?? [];
  const status = panelStatus(items);
  const { icon: Icon, badge } = statusVisuals(status);

  return (
    <section
      className="rounded-lg border bg-card p-4 shadow-sm"
      data-testid="anonymisation-backlog-panel"
      data-status={status}
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">
          {t("admin.anonymisation.title")}
        </h2>
        <Badge
          variant="outline"
          className={cn("gap-1", badge)}
          data-testid="anonymisation-backlog-status"
        >
          <Icon className="h-3.5 w-3.5" aria-hidden="true" />
          {t(`admin.anonymisation.status.${status}`)}
        </Badge>
      </div>

      {items.length === 0 ? (
        <EmptyState
          className="mt-3"
          icon={<CheckCircle2 className="h-5 w-5" aria-hidden="true" />}
          title={t("admin.anonymisation.empty.title")}
          description={t("admin.anonymisation.empty.description")}
          data-testid="anonymisation-backlog-empty"
        />
      ) : (
        <>
          <p className="mt-3 text-sm text-muted-foreground">
            {t("admin.anonymisation.description")}
          </p>
          <ul className="mt-3 divide-y rounded-md border">
            {items.map((item) => (
              <li
                key={item.request_id}
                className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
                data-testid="anonymisation-backlog-row"
                data-waiting-days={item.waiting_days}
              >
                <span className="flex flex-col gap-0.5">
                  <span className="font-mono text-xs text-muted-foreground">
                    {item.subject_user_id}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {t("admin.anonymisation.parties", {
                      requester: item.requested_by_user_id,
                      approver: item.approved_by_user_id ?? "",
                    })}
                  </span>
                </span>
                <span className="flex items-center gap-3 text-xs">
                  <span className="text-muted-foreground">
                    {formatAbsoluteTime(item.approved_at, i18n.language)}
                  </span>
                  <Badge
                    variant="outline"
                    className={cn(
                      "gap-1",
                      item.waiting_days >= OVERDUE_DAYS
                        ? "border-status-danger-border bg-status-danger-subtle text-status-danger-foreground"
                        : "border-status-warning-border bg-status-warning-subtle text-status-warning-foreground",
                    )}
                  >
                    <Clock className="h-3 w-3" aria-hidden="true" />
                    {t("admin.anonymisation.waiting", {
                      count: item.waiting_days,
                    })}
                  </Badge>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
