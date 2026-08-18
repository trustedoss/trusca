// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Status changes waiting on somebody else's agreement.
 *
 * Sits above the component queue because both answer the same question: what
 * is waiting on me. It renders nothing at all when the queue is empty, since
 * most deployments never turn this on and an empty panel would read as a
 * feature that is broken rather than one nobody configured.
 *
 * The requester's own rows stay visible but their buttons are disabled. Hiding
 * them would leave the person who asked wondering whether the request was
 * recorded; showing them greyed says the request is real and is waiting on
 * somebody else.
 */
import { useTranslation } from "react-i18next";

import RelativeTime from "@/components/RelativeTime";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import {
  useDecideTransition,
  usePendingTransitions,
} from "@/features/approvals/useTransitionApprovals";
import { ProblemError } from "@/lib/problem";

interface TransitionApprovalsPanelProps {
  /** The signed-in user, so their own requests render as waiting rather than actionable. */
  currentUserId: string | null;
}

export function TransitionApprovalsPanel({
  currentUserId,
}: TransitionApprovalsPanelProps) {
  const { t } = useTranslation("approvals");
  const query = usePendingTransitions();
  const decide = useDecideTransition();
  const { toast } = useToast();

  if (query.isLoading) {
    return <Skeleton className="h-24 w-full" data-testid="transition-approvals-loading" />;
  }
  const items = query.data?.items ?? [];
  if (items.length === 0) return null;

  const onDecide = (approvalId: string, approve: boolean) => {
    decide.mutate(
      { approvalId, approve, note: null },
      {
        onSuccess: (row) =>
          toast(
            t(
              row.state === "approved"
                ? "transitions.toast.approved"
                : "transitions.toast.rejected",
            ),
            { key: `transition-${row.state}` },
          ),
        onError: (error) =>
          // The server's own sentence, when it sent one: "the person who asked
          // cannot be the one who agrees" says more than a generic failure.
          toast(
            error instanceof ProblemError && error.detail
              ? error.detail
              : t("transitions.toast.failed"),
            { tone: "error", key: "transition-decision-failed" },
          ),
      },
    );
  };

  return (
    <section className="space-y-3" data-testid="transition-approvals-panel">
      <div>
        <h2 className="text-sm font-semibold tracking-tight">
          {t("transitions.title")}
        </h2>
        <p className="text-xs text-muted-foreground">
          {t("transitions.description")}
        </p>
      </div>

      <ul className="divide-y rounded-lg border">
        {items.map((item) => {
          const isMine = currentUserId !== null && item.requested_by_user_id === currentUserId;
          return (
            <li
              key={item.id}
              className="flex flex-wrap items-start justify-between gap-3 p-3"
              data-testid={`transition-approval-${item.id}`}
            >
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex items-center gap-2">
                  <Badge variant="outline" className="font-mono text-xs">
                    {item.target_status}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    <RelativeTime value={item.created_at} />
                  </span>
                </div>
                <p className="text-sm break-words">{item.justification}</p>
              </div>

              <div className="flex items-center gap-2">
                {isMine ? (
                  <span
                    className="text-xs text-muted-foreground"
                    data-testid={`transition-approval-own-${item.id}`}
                  >
                    {t("transitions.waiting_on_someone_else")}
                  </span>
                ) : (
                  <>
                    <Button
                      type="button"
                      size="sm"
                      disabled={decide.isPending}
                      onClick={() => onDecide(item.id, true)}
                      data-testid={`transition-approve-${item.id}`}
                    >
                      {t("transitions.approve")}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={decide.isPending}
                      onClick={() => onDecide(item.id, false)}
                      data-testid={`transition-reject-${item.id}`}
                    >
                      {t("transitions.reject")}
                    </Button>
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
