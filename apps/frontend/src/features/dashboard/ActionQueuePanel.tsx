// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import {
  ClipboardCheck,
  ShieldAlert,
  ShieldX,
  Clock,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import RelativeTime from "@/components/RelativeTime";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useActionQueue } from "@/features/dashboard/api/actionQueue";
import { formatNumber, resolveLocale } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * The action queue — what is waiting on a person, above everything else.
 *
 * This panel is the dashboard's answer to a question a single-scan tool
 * cannot ask. A scan report tells you what a scan found; this tells you what
 * nobody has dealt with yet — approvals sitting unreviewed, known-exploited
 * vulnerabilities past their remediation deadline, builds the gate is
 * blocking, projects that have quietly stopped being scanned. Every one of
 * those needs an account, a team, and history to even exist.
 *
 * It leads the page rather than sitting under the charts because a
 * distribution is context and a queue is work. Reading order is the strongest
 * statement a layout makes about priority.
 */

interface QueueTileProps {
  icon: LucideIcon;
  label: string;
  value: number;
  hint?: string;
  to: string;
  /** Draw attention only when the number means something is wrong. */
  tone?: "neutral" | "warning" | "danger";
  testId: string;
}

function QueueTile({
  icon: Icon,
  label,
  value,
  hint,
  to,
  tone = "neutral",
  testId,
}: QueueTileProps) {
  const { i18n } = useTranslation("dashboard");
  const locale = resolveLocale(i18n);
  const active = value > 0;
  return (
    <Link
      to={to}
      data-testid={testId}
      data-count={value}
      className={cn(
        "flex flex-col gap-1 rounded-md border p-4 transition-colors duration-fast ease-out-soft",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        "hover:bg-accent",
        // Colour is a second signal, never the only one: the count itself and
        // the hint text carry the state for anyone who cannot see the tint.
        active && tone === "danger" && "border-status-danger-border bg-status-danger-subtle",
        active && tone === "warning" && "border-status-warning-border bg-status-warning-subtle",
      )}
    >
      <span className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        <Icon aria-hidden className="h-4 w-4" />
        {label}
      </span>
      <span
        className={cn(
          "text-2xl font-semibold tabular-nums",
          active && tone === "danger" && "text-status-danger-foreground",
          active && tone === "warning" && "text-status-warning-foreground",
        )}
      >
        {/* B4: these tiles sit on the same screen as the KPI cards, share
            their type scale, and the approvals one is literally the same
            number behind the same icon and the same link. Formatting one
            and not the other would put "1,200" above "1200". */}
        {formatNumber(value, locale)}
      </span>
      {hint ? (
        <span className="text-xs text-muted-foreground">{hint}</span>
      ) : null}
    </Link>
  );
}

export function ActionQueuePanel() {
  const { t } = useTranslation("dashboard");
  const query = useActionQueue();

  if (query.isPending) {
    return (
      <Card data-testid="action-queue-loading">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">{t("action_queue.heading")}</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </CardContent>
      </Card>
    );
  }

  // A queue that cannot load is not the same as an empty queue, and saying
  // "nothing to do" when the truth is "we do not know" is the worse of the
  // two mistakes this panel can make.
  if (query.isError) {
    return (
      <Card data-testid="action-queue-error">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">{t("action_queue.heading")}</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          {t("action_queue.error")}
        </CardContent>
      </Card>
    );
  }

  const queue = query.data;
  const kevTotal = queue.kev_sla.overdue + queue.kev_sla.due_soon;
  const nothingWaiting =
    queue.pending_approvals === 0 &&
    kevTotal === 0 &&
    queue.gate_blocked.length === 0 &&
    queue.stale_projects.length === 0;

  return (
    <Card data-testid="action-queue">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{t("action_queue.heading")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <QueueTile
            icon={ClipboardCheck}
            label={t("action_queue.approvals")}
            value={queue.pending_approvals}
            to="/approvals"
            tone="warning"
            testId="action-queue-approvals"
          />
          <QueueTile
            icon={ShieldAlert}
            label={t("action_queue.kev")}
            value={kevTotal}
            hint={
              kevTotal > 0
                ? t("action_queue.kev_split", {
                    overdue: queue.kev_sla.overdue,
                    dueSoon: queue.kev_sla.due_soon,
                  })
                : undefined
            }
            to="/projects"
            tone={queue.kev_sla.overdue > 0 ? "danger" : "warning"}
            testId="action-queue-kev"
          />
          <QueueTile
            icon={ShieldX}
            label={t("action_queue.gate_blocked")}
            value={queue.gate_blocked.length}
            to="/projects"
            tone="danger"
            testId="action-queue-gate"
          />
          <QueueTile
            icon={Clock}
            label={t("action_queue.stale")}
            value={queue.stale_projects.length}
            hint={t("action_queue.stale_hint")}
            to="/projects"
            testId="action-queue-stale"
          />
        </div>

        {nothingWaiting ? (
          <p
            className="text-sm text-muted-foreground"
            data-testid="action-queue-clear"
          >
            {t("action_queue.all_clear")}
          </p>
        ) : null}

        {queue.gate_blocked.length > 0 ? (
          <div data-testid="action-queue-gate-list">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("action_queue.gate_blocked_detail")}
            </h3>
            <ul className="divide-y rounded-md border">
              {queue.gate_blocked.map((entry) => (
                <li key={entry.project_id}>
                  <Link
                    to={`/projects/${entry.project_id}`}
                    className="flex items-center justify-between gap-3 px-3 py-2 text-sm transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                    data-testid={`action-queue-gate-row-${entry.project_id}`}
                  >
                    <span className="truncate font-medium">
                      {entry.project_name}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {t("action_queue.gate_reason", {
                        critical: entry.critical_cve_count,
                        licenses: entry.forbidden_license_count,
                      })}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {queue.stale_projects.length > 0 ? (
          <div data-testid="action-queue-stale-list">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t("action_queue.stale_detail")}
            </h3>
            <ul className="divide-y rounded-md border">
              {queue.stale_projects.map((entry) => (
                <li key={entry.project_id}>
                  <Link
                    to={`/projects/${entry.project_id}`}
                    className="flex items-center justify-between gap-3 px-3 py-2 text-sm transition-colors duration-fast ease-out-soft hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                    data-testid={`action-queue-stale-row-${entry.project_id}`}
                  >
                    <span className="truncate font-medium">
                      {entry.project_name}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {entry.last_succeeded_at ? (
                        <RelativeTime value={entry.last_succeeded_at} />
                      ) : (
                        t("action_queue.never_scanned")
                      )}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
