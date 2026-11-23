// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
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
 *   - Permissions: action buttons only shown for super_admin or group_admin.
 *   - Inline confirm strip before transition (no modal).
 *
 * ETag flow:
 *   The drawer re-fetches the approval (staleTime 0) whenever it opens to
 *   obtain a fresh ETag, then passes it to transitionApproval(). This satisfies
 *   the CLAUDE.md optimistic-concurrency pattern.
 */
import { type ReactNode, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Spinner } from "@/components/primer/Spinner";
import { Alert, AlertDescription } from "@/components/primer/alert";
import { StatusPill, type StatusPillTone } from "@/components/primer/status-pill";
import { Button } from "@/components/primer/Button";
import { PrimerDialog } from "@/components/primer/PrimerDialog";
import { Skeleton } from "@/components/primer/Skeleton";
import { useApprovalDetail, useTransitionApproval } from "@/features/approvals/useApprovals";
import { usePermissions } from "@/hooks/usePermissions";
import RelativeTime from "@/components/RelativeTime";
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

const APPROVAL_PILL_TONE: Record<ApprovalStatus, StatusPillTone> = {
  pending: "warning",
  under_review: "progress",
  approved: "success",
  rejected: "danger",
};

function StatusBadge({ status, t }: StatusBadgeProps) {
  return (
    <StatusPill
      tone={APPROVAL_PILL_TONE[status]}
      data-testid="approval-status-badge"
      data-status={status}
    >
      {t(`approvals.status.${status}`)}
    </StatusPill>
  );
}

// ---------------------------------------------------------------------------
// Meta row
// ---------------------------------------------------------------------------

function Meta({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div>
      <div className="text-xs font-semibold text-muted-foreground">
        {label}
      </div>
      <div className="break-words text-xs">{value}</div>
      {hint ? (
        <div className="break-all font-mono text-xs text-muted-foreground">
          {hint}
        </div>
      ) : null}
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
      className="flex flex-col gap-2 rounded-md border border-status-warning-border bg-status-warning-subtle px-3 py-2 text-sm text-status-warning-foreground"
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
            <Spinner size="small" />
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
  // H-2: group_admin authority comes from the promoted effective role;
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
    <PrimerDialog
      open={open}
      onOpenChange={onOpenChange}
      title={
        approval
              ? (approval.component_name ?? approval.id.slice(0, 8))
              : t("approvals.drawer.component_label")
      }
      description={
        <span className="font-mono text-xs">
          {approval?.component_purl ?? approval?.component_id ?? ""}
        </span>
      }
      position="right"
      width="large"
      testId="approvals-drawer"
    >
      <div className="flex flex-col gap-4">

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
                value={approval.component_name ?? approval.component_id}
                hint={approval.component_purl}
              />
              <Meta
                label={t("approvals.drawer.project_label")}
                value={approval.project_name ?? approval.project_id}
                hint={approval.project_slug}
              />
              <Meta
                label={t("approvals.drawer.team_label")}
                value={approval.team_name ?? approval.team_id}
              />
              {/* B2: the same name the row behind this panel shows. It read
                  the raw id, so opening a drawer from a named row replaced
                  the name with a uuid. */}
              <Meta
                label={t("approvals.drawer.requested_by_label")}
                value={
                  approval.requested_by_name ??
                  approval.requested_by_user_id ??
                  t("approvals.requested_by_unknown")
                }
              />
              <Meta
                label={t("approvals.drawer.requested_at_label")}
                value={
                  <RelativeTime
                    value={approval.requested_at}
                    locale={i18n.resolvedLanguage}
                  />
                }
              />
              {approval.decided_by_user_id ? (
                <Meta
                  label={t("approvals.drawer.decided_by_label")}
                  value={approval.decided_by_name ?? approval.decided_by_user_id}
                />
              ) : null}
              {approval.decided_at ? (
                <Meta
                  label={t("approvals.drawer.decided_at_label")}
                  value={
                    <RelativeTime
                      value={approval.decided_at}
                      locale={i18n.resolvedLanguage}
                    />
                  }
                />
              ) : null}
              {approval.decision_note ? (
                <div className="sm:col-span-2">
                  <div className="text-xs font-semibold text-muted-foreground">
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
      </div>
    </PrimerDialog>
  );
}
