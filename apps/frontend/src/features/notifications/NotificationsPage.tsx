// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * NotificationsPage — Phase 6 chore A2.
 *
 * Two stacked sections under a single page header:
 *
 *   1. Inbox — paginated list of notifications. Filter "Show unread only"
 *      flips the `unread_only` query param. Each row is a button that:
 *        - issues `PATCH /v1/notifications/{id}/read`,
 *        - navigates to `link` if non-null (the SPA route stored on the
 *          row by the backend),
 *        - relies on TanStack Query invalidation to update the list +
 *          bell badge in lock-step.
 *      "Mark all as read" sits at the section header and is rendered
 *      only when `unread_count > 0` so the surface is calm in steady
 *      state.
 *
 *   2. Preferences — four channel toggles backed by the dedicated
 *      `/notification-prefs` endpoint. The "In-app" switch is rendered
 *      but disabled with a tooltip explaining that in-app notifications
 *      are always enabled (defensive: if the row is ever dropped the
 *      backend would default to off, but we never let users opt out
 *      from this surface). Each toggle saves on its own: the PUT fires
 *      on change, the switches wait while it is in flight, and a failed
 *      save puts the switch back and says so inline.
 *
 * Row title and body come from `notificationText`: the catalog message
 * named by `message_key` when this build knows it, the stored English
 * text otherwise.
 *
 * No hardcoded color literals; every tone uses Tailwind tokens. Every
 * visible string flows through `t()` (CLAUDE.md i18n rule).
 */
import {
  AlertIcon,
  BellSlashIcon,
  CheckCircleIcon,
  ChecklistIcon,
  KeyIcon,
  ShieldIcon,
  ShieldXIcon,
  XCircleIcon,
  type Icon,
} from "@primer/octicons-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/primer/alert";
import { Button } from "@/components/primer/Button";
import { Skeleton } from "@/components/primer/Skeleton";
import { Switch } from "@/components/primer/switch";
import { useToast } from "@/components/primer/toast";
import { useClampPage, usePageParam, useUrlFlag } from "@/hooks/useUrlState";
import type {
  NotificationItem,
  NotificationKind,
} from "@/features/notifications/api/notificationsApi";
import type { NotificationPrefs } from "@/features/notifications/api/notificationPrefsApi";
import {
  useMarkAllRead,
  useMarkRead,
  useNotificationPrefs,
  useNotifications,
  useUpdateNotificationPrefs,
} from "@/features/notifications/useNotifications";
import { notificationText } from "@/features/notifications/notificationText";
import { problemMessage } from "@/lib/problemMessage";
import RelativeTime from "@/components/RelativeTime";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;

const KIND_ICONS: Record<NotificationKind, Icon> = {
  scan_completed: CheckCircleIcon,
  scan_failed: XCircleIcon,
  cve_detected: ShieldIcon,
  license_violation: ShieldXIcon,
  approval_pending: ChecklistIcon,
  policy_gate_failed: AlertIcon,
  // H-5 — the requester gets an in-app row when their approval is disposed
  // (migration 0030). Same icon family as approval_pending; the tone below +
  // the translated label distinguish it.
  approval_state_changed: ChecklistIcon,
  // X1 — the daily SLA sweep; a due date passed, nothing new was found.
  vuln_sla_breach: AlertIcon,
  // MAL-2b (#26) — a package already in the project was named by a new
  // advisory. Not a vulnerability to schedule: the artifact is the attack.
  malicious_detected: ShieldXIcon,
  // ER19-3 - something changed about how this person signs in. A key rather
  // than a shield: the shields here mean "a threat was found", and this means
  // "your own sign-in was altered", which needs a different first impression.
  account_security: KeyIcon,
};

const KIND_TONE: Record<NotificationKind, string> = {
  // Pair the icon color with the row content so color is never the only
  // signal — the kind label is also rendered next to the icon.
  scan_completed: "text-status-success-foreground",
  scan_failed: "text-risk-critical",
  cve_detected: "text-risk-high",
  license_violation: "text-risk-high",
  approval_pending: "text-status-info",
  policy_gate_failed: "text-risk-critical",
  approval_state_changed: "text-risk-info",
  vuln_sla_breach: "text-risk-high",
  malicious_detected: "text-risk-critical",
  // Not critical. Most of these are an unlock the person asked for; the ones
  // that matter are the ones they did not, and painting every one red would
  // teach them to skip the colour.
  account_security: "text-risk-high",
};

function NotificationKindIcon({ kind }: { kind: NotificationKind }) {
  const Icon = KIND_ICONS[kind] ?? AlertIcon;
  const tone = KIND_TONE[kind] ?? "text-muted-foreground";
  return <Icon className={cn("h-4 w-4 shrink-0", tone)} aria-hidden />;
}

interface InboxRowProps {
  item: NotificationItem;
  onActivate: (item: NotificationItem) => void;
}

function InboxRow({ item, onActivate }: InboxRowProps) {
  const { t, i18n } = useTranslation("notifications");
  const isUnread = item.read_at === null;
  const text = notificationText(
    item,
    t,
    (key) => i18n.exists(key),
    i18n.resolvedLanguage ?? "en",
  );
  return (
    <li>
      <button
        type="button"
        onClick={() => onActivate(item)}
        data-testid="notifications-row"
        data-notification-id={item.id}
        data-unread={isUnread}
        data-kind={item.kind}
        className={cn(
          "flex w-full items-start gap-3 border-b px-6 py-3 text-left transition-colors duration-fast ease-out-soft",
          "hover:bg-accent/40 focus-visible:bg-accent/40",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          isUnread ? "bg-card" : "bg-muted/20",
        )}
      >
        <div className="pt-0.5">
          <NotificationKindIcon kind={item.kind} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "truncate text-sm",
                isUnread ? "font-semibold" : "font-medium text-muted-foreground",
              )}
            >
              {text.title}
            </span>
            {isUnread ? (
              <span
                aria-hidden
                data-testid="notifications-unread-dot"
                className="inline-block h-2 w-2 shrink-0 rounded-full bg-brand"
              />
            ) : null}
          </div>
          <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
            {text.body}
          </p>
          <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
            {/* The kind is not printed: the icon and the title already say
                it, and the label repeated the title. The guide promises the absolute
                timestamp on hover (M-19); the shared RelativeTime component keeps that tooltip consistent
                with every other relative display. */}
            <RelativeTime
              data-testid="notifications-row-time"
              value={item.created_at}
              locale={i18n.resolvedLanguage}
            />
          </div>
        </div>
      </button>
    </li>
  );
}

interface PreferencesFormProps {
  prefs: NotificationPrefs;
  onSave: (
    next: NotificationPrefs,
    handlers: { onError: (err: unknown) => void },
  ) => void;
  saving: boolean;
}

type OutboundChannel = "email_enabled" | "slack_enabled" | "teams_enabled";

function PreferencesForm({ prefs, onSave, saving }: PreferencesFormProps) {
  const { t } = useTranslation("notifications");
  // What the switches show. Moves on the click, ahead of the server, and is
  // put back to the server's row when a save fails.
  const [shown, setShown] = useState<NotificationPrefs>(prefs);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Re-sync when the server row changes (a save landed, or a refetch).
  useEffect(() => {
    setShown(prefs);
  }, [prefs]);

  function toggle(key: OutboundChannel, value: boolean) {
    // In-app is always sent as on: the server rejects turning it off, and
    // this surface never offers it.
    const next: NotificationPrefs = {
      ...shown,
      [key]: value,
      in_app_enabled: true,
    };
    setShown(next);
    setSaveError(null);
    onSave(next, {
      onError: (err) => {
        setShown(prefs);
        setSaveError(
          problemMessage(err, t, { action: "errors.prefs_save_failed" }),
        );
      },
    });
  }

  return (
    <div className="space-y-4" data-testid="notifications-prefs-form">
      {saveError ? (
        <Alert
          variant="destructive"
          data-testid="notifications-prefs-save-error"
        >
          <AlertDescription>{saveError}</AlertDescription>
        </Alert>
      ) : null}
      <ToggleRow
        label={t("prefs.email.label")}
        description={t("prefs.email.description")}
        checked={shown.email_enabled}
        onCheckedChange={(v) => toggle("email_enabled", v)}
        disabled={saving}
        testId="notifications-prefs-email"
      />
      <ToggleRow
        label={t("prefs.slack.label")}
        description={t("prefs.slack.description")}
        checked={shown.slack_enabled}
        onCheckedChange={(v) => toggle("slack_enabled", v)}
        disabled={saving}
        testId="notifications-prefs-slack"
      />
      <ToggleRow
        label={t("prefs.teams.label")}
        description={t("prefs.teams.description")}
        checked={shown.teams_enabled}
        onCheckedChange={(v) => toggle("teams_enabled", v)}
        disabled={saving}
        testId="notifications-prefs-teams"
      />
      <ToggleRow
        label={t("prefs.in_app.label")}
        description={t("prefs.in_app.description")}
        // Always-true UI affordance: the switch is rendered checked and
        // disabled. The tooltip carries the rationale.
        checked
        disabled
        tooltip={t("prefs.in_app.tooltip")}
        testId="notifications-prefs-in-app"
      />
      {/* Announces the save in progress; the switches carry the result. */}
      <div
        aria-live="polite"
        className="min-h-5 text-xs text-muted-foreground"
        data-testid="notifications-prefs-status"
        data-saving={saving}
      >
        {saving ? t("prefs.saving") : null}
      </div>
    </div>
  );
}

interface ToggleRowProps {
  label: string;
  description: string;
  checked: boolean;
  onCheckedChange?: (checked: boolean) => void;
  disabled?: boolean;
  tooltip?: string;
  testId: string;
}

function ToggleRow({
  label,
  description,
  checked,
  onCheckedChange,
  disabled,
  tooltip,
  testId,
}: ToggleRowProps) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 rounded-md border bg-card p-3",
      )}
      data-testid={`${testId}-row`}
      title={tooltip}
    >
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
      </div>
      <Switch
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        aria-label={label}
        data-testid={testId}
      />
    </div>
  );
}

export function NotificationsPage() {
  const { t } = useTranslation("notifications");
  const navigate = useNavigate();
  // B1: both were component state, so a reload or a Back put the reader on
  // page 1 of everything, whichever page of unread they had been reading.
  const [unreadOnly, setUnreadOnly] = useUrlFlag("unread");
  const [page, setPage] = usePageParam();
  const { toast } = useToast();

  const params = useMemo(
    () => ({ unread_only: unreadOnly, page, page_size: PAGE_SIZE }),
    [unreadOnly, page],
  );

  const listQuery = useNotifications(params);
  const items = listQuery.data?.items ?? [];
  const total = listQuery.data?.total ?? 0;
  const unreadCount = listQuery.data?.unread_count ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  // The page is now something a link or a bookmark can carry, and it can
  // name a page this list no longer has.
  useClampPage(page, totalPages, setPage, listQuery.isSuccess);

  const prefsQuery = useNotificationPrefs();
  const markRead = useMarkRead();
  const markAllRead = useMarkAllRead();
  const updatePrefs = useUpdateNotificationPrefs();

  function notify(text: string, tone: "success" | "error", key?: string) {
    toast(text, { tone, key });
  }

  function handleActivate(item: NotificationItem) {
    // Always mark-read on click — the prompt explicitly groups the two
    // actions: a row click is "I saw this, optionally take me there".
    if (item.read_at === null) {
      markRead.mutate(item.id, {
        onError: (err) => {
          notify(
            problemMessage(err, t, { action: "errors.mark_read_failed" }),
            "error",
            "mark_read_failed",
          );
        },
      });
    }
    if (item.link) {
      navigate(item.link);
    }
  }

  function handleMarkAll() {
    markAllRead.mutate(undefined, {
      onSuccess: () => {
        notify(t("toast.mark_all_done"), "success", "mark_all_done");
      },
      onError: (err) => {
        notify(
          problemMessage(err, t, { action: "errors.mark_all_failed" }),
          "error",
          "mark_all_failed",
        );
      },
    });
  }

  function handleSavePrefs(
    next: NotificationPrefs,
    handlers: { onError: (err: unknown) => void },
  ) {
    // Inline error, no toast: the message belongs next to the switch that
    // moved back, and a success needs no message because the switch is it.
    updatePrefs.mutate(next, { onError: handlers.onError });
  }

  return (
    <div className="flex h-full flex-col" data-testid="notifications-page">
      <PageHeader
        title={t("page.title")}
        description={t("page.subtitle")}
      />

      <div className="flex-1 space-y-8 overflow-y-auto px-6 py-6">
        {/* ---------- Inbox section -------------------------------------- */}
        <section
          className="space-y-3"
          data-testid="notifications-inbox-section"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">
                {t("inbox.section_title")}
              </h2>
              <p className="text-xs text-muted-foreground">
                {t("inbox.section_description")}
              </p>
            </div>
            <div className="flex items-center gap-3">
              <label
                className="flex items-center gap-2 text-xs text-muted-foreground"
                data-testid="notifications-unread-only-label"
              >
                <Switch
                  checked={unreadOnly}
                  // The hook clears the page itself when a filter moves.
                  onCheckedChange={setUnreadOnly}
                  aria-label={t("inbox.unread_only")}
                  data-testid="notifications-unread-only"
                />
                <span>{t("inbox.unread_only")}</span>
              </label>
              {unreadCount > 0 ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={handleMarkAll}
                  disabled={markAllRead.isPending}
                  data-testid="notifications-mark-all"
                >
                  {markAllRead.isPending
                    ? t("inbox.marking_all")
                    : t("inbox.mark_all", { count: unreadCount })}
                </Button>
              ) : null}
            </div>
          </div>

          {listQuery.isError ? (
            <Alert variant="destructive" data-testid="notifications-error">
              <AlertDescription>{t("errors.list_failed")}</AlertDescription>
            </Alert>
          ) : null}

          <div className="overflow-hidden rounded-md border bg-card">
            {listQuery.isLoading ? (
              <ul aria-busy="true" data-testid="notifications-loading">
                {Array.from({ length: 4 }).map((_, i) => (
                  <li key={`skeleton-${i}`} className="border-b px-6 py-3">
                    <Skeleton className="h-12 w-full" />
                  </li>
                ))}
              </ul>
            ) : items.length === 0 ? (
              <EmptyState
                data-testid="notifications-empty"
                icon={<BellSlashIcon />}
                title={unreadOnly ? t("inbox.empty_unread") : t("inbox.empty")}
              />
            ) : (
              <ul data-testid="notifications-list">
                {items.map((item) => (
                  <InboxRow
                    key={item.id}
                    item={item}
                    onActivate={handleActivate}
                  />
                ))}
              </ul>
            )}
          </div>

          {totalPages > 1 ? (
            <div
              className="flex items-center justify-between text-xs"
              data-testid="notifications-pagination"
            >
              <span className="text-muted-foreground">
                {t("inbox.page_label", { page, total: totalPages })}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  data-testid="notifications-page-prev"
                >
                  {t("inbox.previous")}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  data-testid="notifications-page-next"
                >
                  {t("inbox.next")}
                </Button>
              </div>
            </div>
          ) : null}
        </section>

        {/* ---------- Preferences section -------------------------------- */}
        <section
          className="space-y-3"
          data-testid="notifications-prefs-section"
        >
          <div>
            <h2 className="text-base font-semibold">
              {t("prefs.section_title")}
            </h2>
            <p className="text-xs text-muted-foreground">
              {t("prefs.section_description")}
            </p>
          </div>

          {prefsQuery.isLoading ? (
            <div data-testid="notifications-prefs-loading">
              <Skeleton className="h-32 w-full" />
            </div>
          ) : prefsQuery.isError || !prefsQuery.data ? (
            <Alert
              variant="destructive"
              data-testid="notifications-prefs-error"
            >
              <AlertDescription>{t("errors.prefs_load_failed")}</AlertDescription>
            </Alert>
          ) : (
            <PreferencesForm
              prefs={prefsQuery.data}
              onSave={handleSavePrefs}
              saving={updatePrefs.isPending}
            />
          )}
        </section>
      </div>

    </div>
  );
}
