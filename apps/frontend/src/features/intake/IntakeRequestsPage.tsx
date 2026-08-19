// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Asking to use a package before anything has scanned it.
 *
 * The queue only exists where a deployment turned it on, and the shell draws
 * no entry point otherwise. Somebody who arrives anyway (a bookmark, a shared
 * link, a deployment that has since turned it off) is told the deployment does
 * not use this rather than shown an empty list: an empty list reads as "nobody
 * has asked yet" and would leave them waiting for an answer to a question
 * nothing recorded.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardList } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import RelativeTime from "@/components/RelativeTime";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { useDeploymentFeatures } from "@/features/about/api/useDeploymentFeatures";
import type { ApprovalStatus } from "@/lib/approvalsApi";
import {
  type IntakeRequestListOut,
  listIntakeRequests,
  openIntakeRequest,
  transitionIntakeRequest,
} from "@/lib/intakeRequestsApi";
import { problemMessage } from "@/lib/problemMessage";
import { useUIStore } from "@/stores/uiStore";

export function IntakeRequestsPage() {
  const { t } = useTranslation("intake");
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const features = useDeploymentFeatures();
  const enabled = features.intake_requests === true;
  const activeTeamId = useUIStore((s) => s.activeTeamId);

  const [projectId, setProjectId] = useState("");
  const [purl, setPurl] = useState("");
  const [justification, setJustification] = useState("");

  const query = useQuery<IntakeRequestListOut, Error>({
    queryKey: ["intake-requests", activeTeamId ?? "__all__"],
    enabled,
    queryFn: () => listIntakeRequests(),
  });

  const ask = useMutation({
    mutationFn: () =>
      openIntakeRequest({
        project_id: projectId.trim(),
        purl: purl.trim(),
        justification: justification.trim(),
      }),
    meta: { errorToast: false },
    onSuccess: () => {
      setPurl("");
      setJustification("");
      void queryClient.invalidateQueries({ queryKey: ["intake-requests"] });
      toast(t("toast.asked"), { key: "intake-asked" });
    },
    onError: (err) =>
      toast(problemMessage(err, t, { action: "errors.ask_failed" }), {
        tone: "error",
        key: "intake-ask-failed",
      }),
  });

  const decide = useMutation({
    mutationFn: (vars: { id: string; status: ApprovalStatus; version: number }) =>
      transitionIntakeRequest(
        vars.id,
        { status: vars.status, note: null },
        vars.version,
      ),
    meta: { errorToast: false },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["intake-requests"] });
      toast(t("toast.decided"), { key: "intake-decided" });
    },
    onError: (err) =>
      toast(problemMessage(err, t, { action: "errors.decide_failed" }), {
        tone: "error",
        key: "intake-decide-failed",
      }),
  });

  if (!enabled) {
    return (
      <div className="flex h-full flex-col" data-testid="intake-page">
        <PageHeader title={t("title")} description={t("subtitle")} />
        <EmptyState
          icon={<ClipboardList className="h-8 w-8" aria-hidden />}
          title={t("disabled.title")}
          description={t("disabled.description")}
          data-testid="intake-disabled"
        />
      </div>
    );
  }

  const items = query.data?.items ?? [];

  return (
    <div className="flex h-full flex-col" data-testid="intake-page">
      <PageHeader title={t("title")} description={t("subtitle")} />

      <div className="space-y-6 px-6 py-4">
        <form
          className="flex flex-wrap items-end gap-3"
          data-testid="intake-ask-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (projectId.trim() && purl.trim()) ask.mutate();
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="intake-project" className="text-xs">
              {t("field.project")}
            </Label>
            <Input
              id="intake-project"
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              data-testid="intake-project"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="intake-purl" className="text-xs">
              {t("field.purl")}
            </Label>
            <Input
              id="intake-purl"
              value={purl}
              onChange={(event) => setPurl(event.target.value)}
              placeholder="pkg:npm/lodash"
              data-testid="intake-purl"
            />
          </div>
          <div className="min-w-[16rem] flex-1 space-y-1">
            <Label htmlFor="intake-why" className="text-xs">
              {t("field.justification")}
            </Label>
            <Textarea
              id="intake-why"
              rows={2}
              value={justification}
              onChange={(event) => setJustification(event.target.value)}
              data-testid="intake-justification"
            />
          </div>
          <Button
            type="submit"
            size="sm"
            disabled={!projectId.trim() || !purl.trim() || ask.isPending}
            data-testid="intake-ask"
          >
            {t("ask")}
          </Button>
        </form>

        {query.isLoading ? (
          <Skeleton className="h-24 w-full" data-testid="intake-loading" />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<ClipboardList className="h-8 w-8" aria-hidden />}
            title={t("empty.title")}
            description={t("empty.description")}
            data-testid="intake-empty"
          />
        ) : (
          <ul className="divide-y rounded-lg border" data-testid="intake-list">
            {items.map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap items-start justify-between gap-3 p-3"
                data-testid={`intake-row-${row.id}`}
              >
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm break-all">{row.purl}</span>
                    <Badge variant="outline" data-status={row.status}>
                      {t(`status.${row.status}`)}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      <RelativeTime value={row.created_at} />
                    </span>
                  </div>
                  <p className="text-sm break-words">{row.justification}</p>
                </div>
                {row.status === "pending" || row.status === "under_review" ? (
                  <div className="flex items-center gap-2">
                    {row.status === "pending" ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={decide.isPending}
                        onClick={() =>
                          decide.mutate({
                            id: row.id,
                            status: "under_review",
                            version: row.version,
                          })
                        }
                        data-testid={`intake-review-${row.id}`}
                      >
                        {t("action.review")}
                      </Button>
                    ) : (
                      <Button
                        type="button"
                        size="sm"
                        disabled={decide.isPending}
                        onClick={() =>
                          decide.mutate({
                            id: row.id,
                            status: "approved",
                            version: row.version,
                          })
                        }
                        data-testid={`intake-approve-${row.id}`}
                      >
                        {t("action.approve")}
                      </Button>
                    )}
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={decide.isPending}
                      onClick={() =>
                        decide.mutate({
                          id: row.id,
                          status: "rejected",
                          version: row.version,
                        })
                      }
                      data-testid={`intake-reject-${row.id}`}
                    >
                      {t("action.reject")}
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
