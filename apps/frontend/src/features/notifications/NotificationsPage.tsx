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
 *      from this surface). PUT only fires when the form is dirty.
 *
 * No hardcoded color literals; every tone uses Tailwind tokens. Every
 * visible string flows through `t()` (CLAUDE.md i18n rule).
 */
import {
  AlertTriangle,
  Bell,
  BellOff,
  CheckCircle2,
  ClipboardCheck,
  FileWarning,
  ShieldAlert,
  ShieldX,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  AdminToast,
  type AdminToastMessage,
} from "@/features/admin/components/AdminToast";
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
import { ProblemError } from "@/lib/problem";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 20;

const KIND_ICONS: Record<NotificationKind, LucideIcon> = {
  scan_completed: CheckCircle2,
  scan_failed: XCircle,
  cve_detected: ShieldAlert,
  license_violation: ShieldX,
  approval_pending: ClipboardCheck,
  policy_gate_failed: FileWarning,
};

const KIND_TONE: Record<NotificationKind, string> = {
  // Pair the icon color with the row content so color is never the only
  // signal — the kind label is also rendered next to the icon.
  scan_completed: "text-emerald-600",
  scan_failed: "text-risk-critical",
  cve_detected: "text-risk-high",
  license_violation: "text-risk-high",
  approval_pending: "text-risk-low",
  policy_gate_failed: "text-risk-critical",
};

function NotificationKindIcon({ kind }: { kind: NotificationKind }) {
  const Icon = KIND_ICONS[kind] ?? AlertTriangle;
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
              {item.title}
            </span>
            {isUnread ? (
              <span
                aria-hidden
                data-testid="notifications-unread-dot"
                className="inline-block h-2 w-2 shrink-0 rounded-full bg-risk-critical"
              />
            ) : null}
          </div>
          <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
            {item.body}
          </p>
          <div className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
            <span data-testid="notifications-row-kind">
              {t(`kind.${item.kind}`)}
            </span>
            <span aria-hidden>·</span>
            {/* M-19 — the guide promises the absolute timestamp on hover; a
                title attribute keeps the row compact. */}
            <span
              data-testid="notifications-row-time"
              title={new Date(item.created_at).toLocaleString(
                i18n.resolvedLanguage,
              )}
            >
              {formatRelativeToNow(item.created_at, i18n.resolvedLanguage)}
            </span>
          </div>
        </div>
      </button>
    </li>
  );
}

interface PreferencesFormProps {
  prefs: NotificationPrefs;
  onSave: (next: NotificationPrefs) => void;
  saving: boolean;
}

function PreferencesForm({ prefs, onSave, saving }: PreferencesFormProps) {
  const { t } = useTranslation("notifications");
  const [draft, setDraft] = useState<NotificationPrefs>(prefs);

  // Re-sync draft if the underlying server row changes (e.g. mutation
  // invalidation re-fetched and got a different value).
  useEffect(() => {
    setDraft(prefs);
  }, [prefs]);

  const dirty =
    draft.email_enabled !== prefs.email_enabled ||
    draft.slack_enabled !== prefs.slack_enabled ||
    draft.teams_enabled !== prefs.teams_enabled ||
    draft.in_app_enabled !== prefs.in_app_enabled;

  function update<K extends keyof NotificationPrefs>(
    key: K,
    value: NotificationPrefs[K],
  ) {
    setDraft((prev) => ({ ...prev, [key]: value }));
  }

  return (
    <div className="space-y-4" data-testid="notifications-prefs-form">
      <ToggleRow
        label={t("prefs.email.label")}
        description={t("prefs.email.description")}
        checked={draft.email_enabled}
        onCheckedChange={(v) => update("email_enabled", v)}
        testId="notifications-prefs-email"
      />
      <ToggleRow
        label={t("prefs.slack.label")}
        description={t("prefs.slack.description")}
        checked={draft.slack_enabled}
        onCheckedChange={(v) => update("slack_enabled", v)}
        testId="notifications-prefs-slack"
      />
      <ToggleRow
        label={t("prefs.teams.label")}
        description={t("prefs.teams.description")}
        checked={draft.teams_enabled}
        onCheckedChange={(v) => update("teams_enabled", v)}
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

      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          disabled={!dirty || saving}
          onClick={() => onSave(draft)}
          data-testid="notifications-prefs-save"
        >
          {saving ? t("prefs.saving") : t("prefs.save")}
        </Button>
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
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  const toastSeq = useRef(0);

  const params = useMemo(
    () => ({ unread_only: unreadOnly, page, page_size: PAGE_SIZE }),
    [unreadOnly, page],
  );

  const listQuery = useNotifications(params);
  const items = listQuery.data?.items ?? [];
  const total = listQuery.data?.total ?? 0;
  const unreadCount = listQuery.data?.unread_count ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const prefsQuery = useNotificationPrefs();
  const markRead = useMarkRead();
  const markAllRead = useMarkAllRead();
  const updatePrefs = useUpdateNotificationPrefs();

  function notify(text: string, tone: "success" | "error", key?: string) {
    toastSeq.current += 1;
    setToast({ id: toastSeq.current, text, tone, key });
  }

  function handleActivate(item: NotificationItem) {
    // Always mark-read on click — the prompt explicitly groups the two
    // actions: a row click is "I saw this, optionally take me there".
    if (item.read_at === null) {
      markRead.mutate(item.id, {
        onError: (err) => {
          const text =
            err instanceof ProblemError
              ? err.detail || t("errors.mark_read_failed")
              : t("errors.mark_read_failed");
          notify(text, "error", "mark_read_failed");
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
        const text =
          err instanceof ProblemError
            ? err.detail || t("errors.mark_all_failed")
            : t("errors.mark_all_failed");
        notify(text, "error", "mark_all_failed");
      },
    });
  }

  function handleSavePrefs(next: NotificationPrefs) {
    updatePrefs.mutate(next, {
      onSuccess: () => {
        notify(t("toast.prefs_saved"), "success", "prefs_saved");
      },
      onError: (err) => {
        const text =
          err instanceof ProblemError
            ? err.detail || t("errors.prefs_save_failed")
            : t("errors.prefs_save_failed");
        notify(text, "error", "prefs_save_failed");
      },
    });
  }

  return (
    <div className="flex h-full flex-col" data-testid="notifications-page">
      <header className="border-b bg-card px-6 py-4">
        <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <Bell className="h-4 w-4" aria-hidden />
          {t("page.title")}
        </h1>
        <p className="text-sm text-muted-foreground">{t("page.subtitle")}</p>
      </header>

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
                  onCheckedChange={(v) => {
                    setUnreadOnly(v);
                    setPage(1);
                  }}
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
                icon={<BellOff />}
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

      <AdminToast message={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
