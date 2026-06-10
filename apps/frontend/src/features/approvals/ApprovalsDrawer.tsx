/**
 * ApprovalsDrawer — right-slide Sheet detail view for a single approval.
 *
 * Sections:
 *   - Header: approval ID (truncated), status badge.
 *   - Meta grid: component, project, team, requested by/at, decided by/at,
 *     decision note.
 *   - Actions (status-conditional):
 *       pending      → "Start Review"  + "Reject"
 *       under_review → "Approve"       + "Reject"
 *       approved / rejected → read-only (no buttons)
 *   - Permissions: action buttons only shown for super_admin or team_admin.
 *   - Inline confirm strip before transition (no modal).
 *
 * ETag flow:
 *   The drawer re-fetches the approval (staleTime 0) whenever it opens to
 *   obtain a fresh ETag, then passes it to transitionApproval(). This satisfies
 *   the CLAUDE.md optimistic-concurrency pattern.
 */
import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
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
import { Skeleton } from "@/components/ui/skeleton";
import { useApprovalDetail, useTransitionApproval } from "@/features/approvals/useApprovals";
import { usePermissions } from "@/hooks/usePermissions";
import { formatRelativeToNow } from "@/lib/relativeTime";
import { cn } from "@/lib/utils";
import type { ApprovalAction, ApprovalStatus } from "@/lib/approvalsApi";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Props {
  open: boolean;
  approvalId: string | null;
  onOpenChange: (open: boolean) => void;
  notify: (text: string, tone: "success" | "error", key?: string) => void;
}

type ConfirmAction = ApprovalAction | null;

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

interface StatusBadgeProps {
  status: ApprovalStatus;
  t: (key: string) => string;
}

function StatusBadge({ status, t }: StatusBadgeProps) {
  const colorMap: Record<ApprovalStatus, string> = {
    pending:
      "border-yellow-300 bg-yellow-50 text-yellow-700",
    under_review:
      "border-blue-300 bg-blue-50 text-blue-700",
    approved:
      "border-green-300 bg-green-50 text-green-700",
    rejected:
      "border-red-300 bg-red-50 text-red-700",
  };
  return (
    <Badge
      variant="outline"
      className={cn(colorMap[status])}
      data-testid="approval-status-badge"
      data-status={status}
    >
      {t(`approvals.status.${status}`)}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Meta row
// ---------------------------------------------------------------------------

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="break-all font-mono text-xs">{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline confirm strip
// ---------------------------------------------------------------------------

interface ConfirmStripProps {
  action: ApprovalAction;
  isPending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  t: (key: string) => string;
}

function ConfirmStrip({
  action,
  isPending,
  onCancel,
  onConfirm,
  t,
}: ConfirmStripProps) {
  const keyMap: Record<ApprovalAction, string> = {
    under_review: "approvals.confirm.start_review",
    approved: "approvals.confirm.approve",
    rejected: "approvals.confirm.reject",
  };
  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
      data-testid="approvals-confirm-strip"
      data-action={action}
    >
      <p>{t(keyMap[action])}</p>
      <div className="flex justify-end gap-2">
        <Button
          size="sm"
          variant="ghost"
          onClick={onCancel}
          data-testid="approvals-confirm-cancel"
        >
          {t("approvals.action.cancel")}
        </Button>
        <Button
          size="sm"
          onClick={onConfirm}
          disabled={isPending}
          data-testid="approvals-confirm-ok"
        >
          {isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : null}
          {t("approvals.action.confirm")}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ApprovalsDrawer({
  open,
  approvalId,
  onOpenChange,
  notify,
}: Props) {
  const { t, i18n } = useTranslation("approvals");
  // H-2: team_admin authority comes from the promoted effective role —
  // before the promotion this gate never opened for screen-only team admins.
  const { isTeamAdminOrAbove: canAct } = usePermissions();

  const detail = useApprovalDetail(open ? approvalId : null);
  const transition = useTransitionApproval();

  const [pendingAction, setPendingAction] = useState<ConfirmAction>(null);

  // Reset confirm strip when drawer switches to a different approval.
  useEffect(() => {
    setPendingAction(null);
  }, [approvalId]);

  const approval = detail.data?.approval;
  const etag = detail.data?.etag ?? "";

  async function handleTransition(action: ApprovalAction) {
    if (!approval) return;
    try {
      await transition.mutateAsync({ id: approval.id, action, etag });
      setPendingAction(null);
      notify(t("approvals.toast.transitioned"), "success", "transitioned");
    } catch {
      notify(t("approvals.errors.unknown"), "error", "unknown");
    }
  }

  const actionButtons =
    approval?.status === "pending" ? (
      <>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setPendingAction("under_review")}
          data-testid="approvals-action-start-review"
        >
          {t("approvals.action.start_review")}
        </Button>
        <Button
          size="sm"
          variant="destructive"
          onClick={() => setPendingAction("rejected")}
          data-testid="approvals-action-reject"
        >
          {t("approvals.action.reject")}
        </Button>
      </>
    ) : approval?.status === "under_review" ? (
      <>
        <Button
          size="sm"
          onClick={() => setPendingAction("approved")}
          data-testid="approvals-action-approve"
        >
          {t("approvals.action.approve")}
        </Button>
        <Button
          size="sm"
          variant="destructive"
          onClick={() => setPendingAction("rejected")}
          data-testid="approvals-action-reject"
        >
          {t("approvals.action.reject")}
        </Button>
      </>
    ) : null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full max-w-lg flex-col gap-4 overflow-y-auto sm:max-w-lg"
        data-testid="approvals-drawer"
      >
        <SheetHeader>
          <SheetTitle>
            {approval
              ? approval.id.slice(0, 8)
              : t("approvals.drawer.component_label")}
          </SheetTitle>
          <SheetDescription className="font-mono text-xs">
            {approval?.component_id ?? ""}
          </SheetDescription>
        </SheetHeader>

        {detail.isLoading ? (
          <div className="space-y-2" data-testid="approvals-drawer-loading">
            <Skeleton className="h-6 w-1/2" />
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : null}

        {detail.isError ? (
          <Alert variant="destructive">
            <AlertDescription>{t("approvals.errors.unknown")}</AlertDescription>
          </Alert>
        ) : null}

        {approval ? (
          <>
            {/* Status badge */}
            <div className="flex items-center gap-2">
              <StatusBadge status={approval.status} t={t} />
            </div>

            {/* Detail meta grid */}
            <section className="grid grid-cols-1 gap-3 text-xs sm:grid-cols-2">
              <Meta
                label={t("approvals.drawer.component_label")}
                value={approval.component_id}
              />
              <Meta
                label={t("approvals.drawer.project_label")}
                value={approval.project_id}
              />
              <Meta
                label={t("approvals.drawer.team_label")}
                value={approval.team_id}
              />
              <Meta
                label={t("approvals.drawer.requested_by_label")}
                value={approval.requested_by_user_id ?? "—"}
              />
              <Meta
                label={t("approvals.drawer.requested_at_label")}
                value={formatRelativeToNow(
                  approval.requested_at,
                  i18n.resolvedLanguage,
                )}
              />
              {approval.decided_by_user_id ? (
                <Meta
                  label={t("approvals.drawer.decided_by_label")}
                  value={approval.decided_by_user_id}
                />
              ) : null}
              {approval.decided_at ? (
                <Meta
                  label={t("approvals.drawer.decided_at_label")}
                  value={formatRelativeToNow(
                    approval.decided_at,
                    i18n.resolvedLanguage,
                  )}
                />
              ) : null}
              {approval.decision_note ? (
                <div className="sm:col-span-2">
                  <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                    {t("approvals.drawer.decision_note_label")}
                  </div>
                  <div className="mt-1 rounded-md border bg-muted/30 px-3 py-2 text-xs">
                    {approval.decision_note}
                  </div>
                </div>
              ) : null}
            </section>

            {/* Action buttons (role-gated) */}
            {canAct && actionButtons ? (
              <section
                className="flex flex-wrap items-center gap-2 border-t pt-4"
                data-testid="approvals-drawer-actions"
              >
                {actionButtons}
              </section>
            ) : null}

            {/* Inline confirm strip */}
            {pendingAction ? (
              <ConfirmStrip
                action={pendingAction}
                isPending={transition.isPending}
                onCancel={() => setPendingAction(null)}
                onConfirm={() => handleTransition(pendingAction)}
                t={t}
              />
            ) : null}
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
