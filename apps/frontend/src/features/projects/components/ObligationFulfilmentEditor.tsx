// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { ObligationFulfilmentSummary } from "@/features/projects/api/obligationsApi";
import {
  useClearObligationFulfilment,
  useRecordObligationFulfilment,
} from "@/features/projects/api/useObligationFulfilment";
import { ObligationFulfilmentBadge } from "@/features/projects/components/ObligationFulfilmentBadge";
import {
  OBLIGATION_FULFILMENT_STATUSES,
  type ObligationFulfilmentStatus,
} from "@/lib/obligationConstants";
import { problemMessage } from "@/lib/problemMessage";
import { useAuthStore } from "@/stores/authStore";

/**
 * Recording what this project did about one obligation (N15).
 *
 * Lives in the drawer rather than the row: it asks for a note and a link, and
 * an inline control that only offered the status would push everybody towards
 * marking things done with no record of what was done.
 *
 * The version the editor opened on rides along as If-Match, so a save made
 * against a stale read is refused instead of overwriting whatever somebody
 * else recorded in between. That is a real case here: the release engineer and
 * the person tracking compliance are often looking at the same obligation in
 * the same hour.
 *
 * Assignment is deliberately limited to the person using it. Naming somebody
 * else needs a member list this screen has no permission to read, and a name
 * makes the row look owned; leaving it unassigned at least reads as waiting.
 */

export interface ObligationFulfilmentEditorProps {
  projectId: string;
  obligationId: string;
  fulfilment: ObligationFulfilmentSummary | null;
}

export function ObligationFulfilmentEditor({
  projectId,
  obligationId,
  fulfilment,
}: ObligationFulfilmentEditorProps) {
  const { t } = useTranslation("project_detail");
  const currentUserId = useAuthStore((state) => state.user?.id ?? null);
  const record = useRecordObligationFulfilment(projectId, obligationId);
  const clear = useClearObligationFulfilment(projectId, obligationId);

  const [status, setStatus] = useState<ObligationFulfilmentStatus>(
    fulfilment?.status ?? "not_started",
  );
  const [dueOn, setDueOn] = useState(fulfilment?.due_on ?? "");
  const [note, setNote] = useState(fulfilment?.evidence_note ?? "");
  const [url, setUrl] = useState(fulfilment?.evidence_url ?? "");
  const [assignedToMe, setAssignedToMe] = useState(
    fulfilment?.assignee_user_id != null &&
      fulfilment.assignee_user_id === currentUserId,
  );

  // Re-seed when the drawer is pointed at a different obligation, and when a
  // save returns a record the server changed (the completion stamp). Without
  // this the fields keep the previous obligation's answers, which is the way
  // somebody records the wrong thing while believing they read it.
  useEffect(() => {
    setStatus(fulfilment?.status ?? "not_started");
    setDueOn(fulfilment?.due_on ?? "");
    setNote(fulfilment?.evidence_note ?? "");
    setUrl(fulfilment?.evidence_url ?? "");
    setAssignedToMe(
      fulfilment?.assignee_user_id != null &&
        fulfilment.assignee_user_id === currentUserId,
    );
  }, [obligationId, fulfilment, currentUserId]);

  const somebodyElseOwnsIt =
    fulfilment?.assignee_user_id != null &&
    fulfilment.assignee_user_id !== currentUserId;

  function save() {
    record.mutate({
      status,
      due_on: dueOn.length > 0 ? dueOn : null,
      evidence_note: note.trim().length > 0 ? note.trim() : null,
      evidence_url: url.trim().length > 0 ? url.trim() : null,
      assignee_user_id: assignedToMe
        ? currentUserId
        : somebodyElseOwnsIt
          ? fulfilment.assignee_user_id
          : null,
      ifMatchVersion: fulfilment?.version ?? null,
    });
  }

  return (
    <section
      className="flex flex-col gap-3"
      data-testid="obligation-fulfilment-editor"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">
          {t("obligations.fulfilment.section")}
        </h3>
        <ObligationFulfilmentBadge status={fulfilment?.status ?? null} />
      </div>

      <p className="text-xs text-muted-foreground">
        {t("obligations.fulfilment.help")}
      </p>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="obligation-fulfilment-status">
          {t("obligations.fulfilment.field.status")}
        </Label>
        <select
          id="obligation-fulfilment-status"
          className="h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={status}
          onChange={(e) =>
            setStatus(e.target.value as ObligationFulfilmentStatus)
          }
          data-testid="obligation-fulfilment-status-select"
        >
          {OBLIGATION_FULFILMENT_STATUSES.map((option) => (
            <option key={option} value={option}>
              {t(`obligations.fulfilment.status.${option}`)}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="obligation-fulfilment-due">
          {t("obligations.fulfilment.field.due_on")}
        </Label>
        <Input
          id="obligation-fulfilment-due"
          type="date"
          value={dueOn}
          onChange={(e) => setDueOn(e.target.value)}
          data-testid="obligation-fulfilment-due-input"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="obligation-fulfilment-note">
          {t("obligations.fulfilment.field.evidence_note")}
        </Label>
        <Textarea
          id="obligation-fulfilment-note"
          rows={3}
          maxLength={4000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t("obligations.fulfilment.field.evidence_note_placeholder")}
          data-testid="obligation-fulfilment-note-input"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="obligation-fulfilment-url">
          {t("obligations.fulfilment.field.evidence_url")}
        </Label>
        <Input
          id="obligation-fulfilment-url"
          type="url"
          maxLength={2048}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://"
          aria-describedby="obligation-fulfilment-url-help"
          data-testid="obligation-fulfilment-url-input"
        />
        <p
          id="obligation-fulfilment-url-help"
          className="text-xs text-muted-foreground"
        >
          {t("obligations.fulfilment.field.evidence_url_help")}
        </p>
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="h-4 w-4 rounded border-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          checked={assignedToMe}
          disabled={somebodyElseOwnsIt}
          onChange={(e) => setAssignedToMe(e.target.checked)}
          data-testid="obligation-fulfilment-assign-me"
        />
        <span>
          {somebodyElseOwnsIt
            ? t("obligations.fulfilment.field.assigned_elsewhere")
            : t("obligations.fulfilment.field.assign_to_me")}
        </span>
      </label>

      {record.isError ? (
        <Alert variant="destructive" data-testid="obligation-fulfilment-error">
          <AlertDescription>
            {problemMessage(record.error, t, {
              action: "obligations.fulfilment.errors.save",
            })}
          </AlertDescription>
        </Alert>
      ) : null}

      {clear.isError ? (
        <Alert
          variant="destructive"
          data-testid="obligation-fulfilment-clear-error"
        >
          <AlertDescription>
            {problemMessage(clear.error, t, {
              action: "obligations.fulfilment.errors.clear",
            })}
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          onClick={save}
          disabled={record.isPending}
          data-testid="obligation-fulfilment-save"
        >
          {record.isPending
            ? t("obligations.fulfilment.action.saving")
            : t("obligations.fulfilment.action.save")}
        </Button>
        {fulfilment !== null ? (
          <Button
            type="button"
            variant="ghost"
            onClick={() => clear.mutate()}
            disabled={clear.isPending}
            data-testid="obligation-fulfilment-clear"
          >
            {t("obligations.fulfilment.action.clear")}
          </Button>
        ) : null}
        {record.isSuccess && !record.isPending ? (
          <span
            className="text-xs text-muted-foreground"
            data-testid="obligation-fulfilment-saved"
          >
            {t("obligations.fulfilment.saved")}
          </span>
        ) : null}
      </div>

      {fulfilment?.completed_at != null ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="obligation-fulfilment-completed-at"
        >
          {t("obligations.fulfilment.completed_at", {
            when: new Date(fulfilment.completed_at).toLocaleString(),
          })}
        </p>
      ) : null}
    </section>
  );
}
