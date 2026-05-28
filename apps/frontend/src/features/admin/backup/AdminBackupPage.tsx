/**
 * AdminBackupPage — Phase 6 PR #19 chore D.
 *
 * Top action bar:
 *   - "Run manual backup now" → triggers a Celery task that writes
 *     `manual-<timestamp>.tar.gz` into the backup volume.
 *   - File input + restore flow: when a file is selected the page renders a
 *     warning banner and a typing-gate text input. The user must type
 *     "restore" exactly (case-sensitive) to enable the destructive action.
 *
 * Compact 40px-row table mirroring `AdminUsersPage`:
 *   name (mono) | kind badge | created_at (relative + ISO tooltip) | size
 *   (human) | db_revision (last 7 chars, mono) | actions (Download / Delete).
 *
 * Auto rows render the Delete control as a disabled tooltip stub
 * ("Auto backups are pruned after 7 days") — the backend returns 409 on
 * DELETE for `auto-*` rows; surfacing the rule in the UI saves the round
 * trip and aligns with the existing "no destructive button if it can't
 * succeed" pattern.
 *
 * Inline confirmation strip on Delete mirrors `AdminUserDrawer`
 * (`ConfirmStrip`) — no modal, ESC closes the row's pending state.
 *
 * After a successful restore upload the page renders a persistent banner
 * informing the operator that the backend will pause and asking them to
 * reload after 30 seconds. We deliberately do NOT auto-reload; the operator
 * may want to capture log lines first.
 */
import { Database, Download, Loader2, RefreshCw, Trash2, UploadCloud } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AdminToast,
  type AdminToastMessage,
} from "@/features/admin/components/AdminToast";
import {
  adminErrorExtension,
  adminErrorMessageKey,
} from "@/features/admin/lib/adminErrorMessage";
import {
  useAdminBackups,
  useDeleteBackup,
  useDownloadBackup,
  useTriggerManualBackup,
  useUploadRestore,
} from "@/features/admin/backup/useAdminBackups";
import type {
  BackupInfo,
  BackupKind,
} from "@/features/admin/api/adminBackupsApi";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";

/** Literal token the user must type to enable the restore button. */
const RESTORE_CONFIRM_TOKEN = "restore";

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || Number.isNaN(bytes)) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

function shortRevision(rev: string | null | undefined): string {
  if (!rev) return "—";
  if (rev.length <= 7) return rev;
  return rev.slice(-7);
}

function kindBadgeClass(kind: BackupKind): string {
  // Auto = blue (informational, system-driven); Manual = neutral gray.
  // Pair color with the literal label so the badge is not color-only.
  return kind === "auto"
    ? "border-blue-300 bg-blue-50 text-blue-700"
    : "border-muted bg-muted text-muted-foreground";
}

export function AdminBackupPage() {
  const { t, i18n } = useTranslation("admin");

  const backupsQuery = useAdminBackups();
  const triggerManual = useTriggerManualBackup();
  const downloadMut = useDownloadBackup();
  const deleteMut = useDeleteBackup();
  const uploadMut = useUploadRestore();

  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  const toastSeq = useRef(0);

  const [pendingDeleteName, setPendingDeleteName] = useState<string | null>(
    null,
  );

  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreConfirmInput, setRestoreConfirmInput] = useState("");
  const [restoreQueued, setRestoreQueued] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // When the page is reopened (e.g. tab regains focus after a restore),
  // make sure stale confirm-strip state never lingers across data changes.
  useEffect(() => {
    setPendingDeleteName(null);
  }, [backupsQuery.dataUpdatedAt]);

  function notify(text: string, tone: "success" | "error", key?: string) {
    toastSeq.current += 1;
    setToast({ id: toastSeq.current, text, tone, key });
  }

  async function handleManualTrigger() {
    try {
      await triggerManual.mutateAsync();
      notify(
        t("admin.backup.toast.manual_triggered"),
        "success",
        "manual_triggered",
      );
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
    }
  }

  async function handleDownload(name: string) {
    try {
      await downloadMut.mutateAsync(name);
      notify(
        t("admin.backup.toast.download_started"),
        "success",
        "download_started",
      );
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
    }
  }

  async function handleDeleteConfirm(name: string) {
    try {
      await deleteMut.mutateAsync(name);
      setPendingDeleteName(null);
      notify(t("admin.backup.toast.deleted"), "success", "deleted");
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
    }
  }

  function handleFilePick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null;
    setRestoreFile(file);
    setRestoreConfirmInput("");
    setRestoreQueued(false);
  }

  function clearRestoreSelection() {
    setRestoreFile(null);
    setRestoreConfirmInput("");
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  async function handleRestore() {
    if (!restoreFile) return;
    if (restoreConfirmInput !== RESTORE_CONFIRM_TOKEN) return;
    try {
      await uploadMut.mutateAsync(restoreFile);
      setRestoreQueued(true);
      clearRestoreSelection();
      notify(
        t("admin.backup.toast.restore_queued"),
        "success",
        "restore_queued",
      );
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
    }
  }

  const items = backupsQuery.data?.items ?? [];

  const restoreEnabled = useMemo(() => {
    return (
      restoreFile != null &&
      restoreConfirmInput === RESTORE_CONFIRM_TOKEN &&
      !uploadMut.isPending
    );
  }, [restoreFile, restoreConfirmInput, uploadMut.isPending]);

  return (
    <div className="flex h-full flex-col" data-testid="admin-backup-page">
      <header className="border-b bg-card px-6 py-4">
        <h1 className="text-lg font-semibold tracking-tight">
          {t("admin.backup.title")}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("admin.backup.subtitle")}
        </p>
      </header>

      {restoreQueued ? (
        <div className="border-b bg-amber-50 px-6 py-3" data-testid="admin-backup-restore-queued">
          <Alert className="border-amber-300 bg-amber-50 text-amber-900">
            <AlertDescription>
              {t("admin.backup.restore.queued_banner")}
            </AlertDescription>
          </Alert>
        </div>
      ) : null}

      <div
        className="flex flex-wrap items-center justify-between gap-3 border-b bg-card px-6 py-3"
        data-testid="admin-backup-toolbar"
      >
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={handleManualTrigger}
            disabled={triggerManual.isPending}
            data-testid="admin-backup-manual-trigger"
          >
            {triggerManual.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Database className="h-4 w-4" aria-hidden />
            )}
            {t("admin.backup.actions.manual_trigger")}
          </Button>
          <label className="inline-flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".tar.gz,application/gzip,application/x-tar"
              onChange={handleFilePick}
              className="sr-only"
              data-testid="admin-backup-file-input"
            />
            <Button
              size="sm"
              variant="outline"
              type="button"
              onClick={() => fileInputRef.current?.click()}
              data-testid="admin-backup-file-picker"
            >
              <UploadCloud className="h-4 w-4" aria-hidden />
              {t("admin.backup.actions.choose_file")}
            </Button>
            {restoreFile ? (
              <span
                className="font-mono text-xs text-muted-foreground"
                data-testid="admin-backup-file-name"
              >
                {restoreFile.name}
              </span>
            ) : null}
          </label>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => backupsQuery.refetch()}
            disabled={backupsQuery.isFetching}
            data-testid="admin-backup-refresh"
          >
            <RefreshCw
              className={cn(
                "h-4 w-4",
                backupsQuery.isFetching && "animate-spin",
              )}
              aria-hidden
            />
            {t("admin.backup.actions.refresh")}
          </Button>
        </div>
      </div>

      {restoreFile ? (
        <div
          className="border-b bg-amber-50 px-6 py-3"
          data-testid="admin-backup-restore-strip"
        >
          <Alert className="border-amber-300 bg-amber-50 text-amber-900">
            <AlertDescription className="space-y-3">
              <p data-testid="admin-backup-restore-warning">
                {t("admin.backup.restore.warning")}
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Label
                  htmlFor="admin-backup-restore-confirm"
                  className="text-xs text-amber-900"
                >
                  {t("admin.backup.restore.type_to_confirm", {
                    token: RESTORE_CONFIRM_TOKEN,
                  })}
                </Label>
                <Input
                  id="admin-backup-restore-confirm"
                  data-testid="admin-backup-restore-confirm"
                  value={restoreConfirmInput}
                  onChange={(e) => setRestoreConfirmInput(e.target.value)}
                  className="h-9 w-40 font-mono text-xs"
                  autoComplete="off"
                  spellCheck={false}
                />
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={handleRestore}
                  disabled={!restoreEnabled}
                  data-testid="admin-backup-restore-submit"
                >
                  {uploadMut.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : null}
                  {t("admin.backup.actions.restore")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={clearRestoreSelection}
                  data-testid="admin-backup-restore-cancel"
                >
                  {t("admin.actions.cancel")}
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        </div>
      ) : null}

      <div className="flex-1 overflow-y-auto">
        {backupsQuery.isError ? (
          <div className="px-6 py-4">
            <Alert variant="destructive" data-testid="admin-backup-error">
              <AlertDescription>{t("admin.errors.unknown")}</AlertDescription>
            </Alert>
          </div>
        ) : null}

        <table
          className="w-full text-sm"
          data-testid="admin-backup-table"
          aria-busy={backupsQuery.isLoading}
        >
          <thead className="sticky top-0 bg-card">
            <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-6 py-2">{t("admin.backup.column.name")}</th>
              <th className="px-3 py-2">{t("admin.backup.column.kind")}</th>
              <th className="px-3 py-2">{t("admin.backup.column.created_at")}</th>
              <th className="px-3 py-2 text-right">
                {t("admin.backup.column.size")}
              </th>
              <th className="px-3 py-2">{t("admin.backup.column.db_revision")}</th>
              <th className="px-3 py-2 text-right">
                {t("admin.backup.column.actions")}
              </th>
            </tr>
          </thead>
          <tbody data-testid="admin-backup-tbody">
            {backupsQuery.isLoading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <tr key={`skeleton-${i}`} className="border-b">
                  <td className="px-6 py-2" colSpan={6}>
                    <Skeleton className="h-5 w-full" />
                  </td>
                </tr>
              ))
            ) : items.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-6 py-12 text-center text-sm text-muted-foreground"
                  data-testid="admin-backup-empty"
                >
                  {t("admin.backup.empty")}
                </td>
              </tr>
            ) : (
              items.map((item) => (
                <BackupRow
                  key={item.name}
                  item={item}
                  locale={i18n.resolvedLanguage}
                  pendingDeleteName={pendingDeleteName}
                  onRequestDelete={() => setPendingDeleteName(item.name)}
                  onCancelDelete={() => setPendingDeleteName(null)}
                  onConfirmDelete={() => handleDeleteConfirm(item.name)}
                  onDownload={() => handleDownload(item.name)}
                  isDeletePending={deleteMut.isPending}
                  isDownloadPending={downloadMut.isPending}
                />
              ))
            )}
          </tbody>
        </table>
      </div>

      <AdminToast message={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}

interface BackupRowProps {
  item: BackupInfo;
  locale: string | undefined;
  pendingDeleteName: string | null;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
  onDownload: () => void;
  isDeletePending: boolean;
  isDownloadPending: boolean;
}

function BackupRow({
  item,
  locale,
  pendingDeleteName,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
  onDownload,
  isDeletePending,
  isDownloadPending,
}: BackupRowProps) {
  const { t } = useTranslation("admin");
  const isAuto = item.kind === "auto";
  const showConfirm = pendingDeleteName === item.name;
  const relative = formatRelativeToNow(item.created_at, locale);

  return (
    <>
      <tr
        data-testid="admin-backup-row"
        data-name={item.name}
        data-kind={item.kind}
        className="border-b transition-colors duration-fast ease-out-soft hover:bg-accent/40"
        style={{ height: "var(--table-row)" }}
      >
        <td className="truncate px-6 font-mono text-xs">{item.name}</td>
        <td className="px-3">
          <Badge
            variant="outline"
            className={cn(kindBadgeClass(item.kind))}
            data-testid="admin-backup-kind-badge"
          >
            {t(`admin.backup.kind.${item.kind}`)}
          </Badge>
        </td>
        <td className="px-3 text-xs text-muted-foreground">
          <span title={item.created_at}>{relative}</span>
        </td>
        <td className="px-3 text-right text-xs text-muted-foreground">
          {formatBytes(item.size_bytes)}
        </td>
        <td className="px-3 font-mono text-xs text-muted-foreground">
          {shortRevision(item.db_revision)}
        </td>
        <td className="px-3">
          <div className="flex items-center justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={onDownload}
              disabled={isDownloadPending}
              data-testid="admin-backup-action-download"
            >
              <Download className="h-4 w-4" aria-hidden />
              {t("admin.backup.actions.download")}
            </Button>
            {isAuto ? (
              <Button
                size="sm"
                variant="ghost"
                disabled
                title={t("admin.backup.tooltip.auto_pruned")}
                data-testid="admin-backup-action-delete-disabled"
              >
                <Trash2 className="h-4 w-4" aria-hidden />
                {t("admin.backup.actions.delete")}
              </Button>
            ) : (
              <Button
                size="sm"
                variant="destructive"
                onClick={onRequestDelete}
                data-testid="admin-backup-action-delete"
              >
                <Trash2 className="h-4 w-4" aria-hidden />
                {t("admin.backup.actions.delete")}
              </Button>
            )}
          </div>
        </td>
      </tr>
      {showConfirm ? (
        <tr
          className="border-b bg-amber-50/50"
          data-testid="admin-backup-confirm-strip"
          data-name={item.name}
        >
          <td colSpan={6} className="px-6 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              <span>{t("admin.backup.confirm.delete", { name: item.name })}</span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={onCancelDelete}
                  data-testid="admin-backup-confirm-cancel"
                >
                  {t("admin.actions.cancel")}
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={onConfirmDelete}
                  disabled={isDeletePending}
                  data-testid="admin-backup-confirm-ok"
                >
                  {isDeletePending ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : null}
                  {t("admin.actions.confirm")}
                </Button>
              </div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}
