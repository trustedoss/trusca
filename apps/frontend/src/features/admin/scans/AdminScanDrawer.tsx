/**
 * AdminScanDrawer — right-slide Sheet detail view for a scan in the queue.
 *
 * The list endpoint already carries everything the drawer needs (project +
 * team join, status, kind, started/finished, duration, error, requested_by)
 * so we render directly off the row payload — no second fetch.
 *
 * The "Cancel scan" action uses an inline confirm strip (PR #13 pattern)
 * before dispatching the mutation. Status-illegal transitions (already
 * cancelled / succeeded / failed) surface as a toast keyed by the
 * `scan_already_cancelled` Problem extension.
 */
import { Loader2 } from "lucide-react";
import { type ReactNode, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  AdminToast,
  type AdminToastMessage,
} from "@/features/admin/components/AdminToast";
import {
  type AdminScanListItem,
  type AdminScanStatus,
} from "@/features/admin/scans/api/adminScansApi";
import { useCancelAdminScan } from "@/features/admin/scans/api/useAdminScans";
import {
  adminErrorExtension,
  adminErrorMessageKey,
} from "@/features/admin/lib/adminErrorMessage";
import RelativeTime from "@/components/RelativeTime";
import { cn } from "@/lib/utils";

interface AdminScanDrawerProps {
  open: boolean;
  scan: AdminScanListItem | null;
  onOpenChange: (open: boolean) => void;
}

export function statusTone(
  status: AdminScanStatus,
): "ok" | "running" | "down" | "muted" {
  if (status === "succeeded") return "ok";
  if (status === "running") return "running";
  if (status === "failed") return "down";
  return "muted";
}

export function ScanStatusBadge({ status }: { status: AdminScanStatus }) {
  const { t } = useTranslation("admin");
  const tone = statusTone(status);
  return (
    <Badge
      variant="outline"
      data-testid="admin-scan-status-badge"
      data-status={status}
      data-tone={tone}
      className={cn(
        "gap-1 font-mono text-xs",
        tone === "ok" && "border-emerald-300 bg-emerald-50 text-emerald-700",
        tone === "running" &&
          "border-blue-300 bg-blue-50 text-blue-700",
        tone === "down" && "border-red-300 bg-red-50 text-red-700",
        tone === "muted" && "border-muted bg-muted text-muted-foreground",
      )}
    >
      {t(`admin.scans.status.${status}`)}
    </Badge>
  );
}

export function AdminScanDrawer({
  open,
  scan,
  onOpenChange,
}: AdminScanDrawerProps) {
  const { t, i18n } = useTranslation("admin");
  const cancel = useCancelAdminScan();
  const [confirming, setConfirming] = useState(false);
  const [toast, setToast] = useState<AdminToastMessage | null>(null);
  const toastSeq = useRef(0);

  function notify(text: string, tone: "success" | "error", key?: string) {
    toastSeq.current += 1;
    setToast({ id: toastSeq.current, text, tone, key });
  }

  async function handleCancel() {
    if (!scan) return;
    try {
      await cancel.mutateAsync({ scanId: scan.id });
      notify(t("admin.scans.toast.cancelled"), "success", "cancelled");
      setConfirming(false);
      onOpenChange(false);
    } catch (err) {
      notify(t(adminErrorMessageKey(err)), "error", adminErrorExtension(err));
      setConfirming(false);
    }
  }

  const cancellable =
    scan?.status === "queued" || scan?.status === "running" ? true : false;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full max-w-lg flex-col gap-4 overflow-y-auto sm:max-w-lg"
        data-testid="admin-scan-drawer"
      >
        <SheetHeader>
          <SheetTitle>
            {scan
              ? t("admin.scans.drawer.title", { id: scan.id.slice(0, 8) })
              : t("admin.scans.title")}
          </SheetTitle>
          <SheetDescription className="font-mono text-xs">
            {scan?.id ?? ""}
          </SheetDescription>
        </SheetHeader>

        {scan ? (
          <>
            <section
              className="flex flex-wrap items-center gap-2"
              data-testid="admin-scan-drawer-meta"
            >
              <ScanStatusBadge status={scan.status} />
              <Badge
                variant="outline"
                className="bg-muted text-xs text-muted-foreground"
                data-testid="admin-scan-kind"
              >
                {scan.kind}
              </Badge>
            </section>

            <section className="grid grid-cols-2 gap-3 text-xs">
              <Meta
                label={t("admin.scans.drawer.project_label")}
                value={scan.project_name}
                testId="admin-scan-project"
              />
              <Meta
                label={t("admin.scans.drawer.team_label")}
                value={scan.team_name}
                testId="admin-scan-team"
              />
              <Meta
                label={t("admin.scans.drawer.started_at_label")}
                value={
                  scan.started_at ? (
                    <RelativeTime
                      value={scan.started_at}
                      locale={i18n.resolvedLanguage}
                    />
                  ) : (
                    "—"
                  )
                }
              />
              <Meta
                label={t("admin.scans.drawer.finished_at_label")}
                value={
                  scan.finished_at ? (
                    <RelativeTime
                      value={scan.finished_at}
                      locale={i18n.resolvedLanguage}
                    />
                  ) : (
                    "—"
                  )
                }
              />
              <Meta
                label={t("admin.scans.drawer.duration_label")}
                value={
                  scan.duration_seconds == null
                    ? "—"
                    : `${scan.duration_seconds.toFixed(1)}s`
                }
              />
              <Meta
                label={t("admin.scans.drawer.requested_by_label")}
                value={scan.requested_by_user_id ?? "—"}
              />
            </section>

            <section
              data-testid="admin-scan-error-section"
              className="space-y-1"
            >
              <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
                {t("admin.scans.drawer.error_label")}
              </span>
              {scan.error_message ? (
                <Alert variant="destructive">
                  <AlertDescription
                    className="font-mono text-xs"
                    data-testid="admin-scan-error-message"
                  >
                    {scan.error_message}
                  </AlertDescription>
                </Alert>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {t("admin.scans.drawer.no_error")}
                </p>
              )}
            </section>

            {cancellable ? (
              <section
                className="flex flex-wrap items-center gap-2 border-t pt-4"
                data-testid="admin-scan-actions"
              >
                {!confirming ? (
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => setConfirming(true)}
                    data-testid="admin-scan-action-cancel"
                  >
                    {t("admin.scans.actions.cancel")}
                  </Button>
                ) : (
                  <div
                    className="flex w-full flex-col gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
                    data-testid="admin-scan-confirm-strip"
                  >
                    <p>{t("admin.scans.confirm.cancel")}</p>
                    <div className="flex justify-end gap-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setConfirming(false)}
                        data-testid="admin-scan-confirm-cancel-btn"
                      >
                        {t("admin.actions.cancel")}
                      </Button>
                      <Button
                        size="sm"
                        onClick={handleCancel}
                        disabled={cancel.isPending}
                        data-testid="admin-scan-confirm-ok"
                      >
                        {cancel.isPending ? (
                          <Loader2
                            className="h-4 w-4 animate-spin"
                            aria-hidden
                          />
                        ) : null}
                        {t("admin.actions.confirm")}
                      </Button>
                    </div>
                  </div>
                )}
              </section>
            ) : null}
          </>
        ) : null}

        <AdminToast message={toast} onDismiss={() => setToast(null)} />
      </SheetContent>
    </Sheet>
  );
}

interface MetaProps {
  label: string;
  value: ReactNode;
  testId?: string;
}

function Meta({ label, value, testId }: MetaProps) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="text-sm" data-testid={testId}>
        {value}
      </div>
    </div>
  );
}
