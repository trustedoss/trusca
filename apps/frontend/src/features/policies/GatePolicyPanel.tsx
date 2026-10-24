// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Build-gate thresholds for one team.
 *
 * The screen has to make one distinction the form controls do not make on
 * their own: a field left alone is not the same as a field set to nothing. An
 * empty threshold means the team has not decided and keeps following its
 * organization, and 0 means the team decided that any score blocks. A single
 * number input cannot say which the user meant, so each field carries an
 * explicit override switch and the inherited value is shown beside it.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  APPROVABLE_STATUSES,
  type GatePolicyBasis,
  type GatePolicyUpsertIn,
  isGatePolicyConflict,
} from "@/lib/gatePoliciesApi";

import {
  useDeleteTeamGatePolicy,
  useEpssAvailability,
  useTeamGatePolicy,
  useUpsertTeamGatePolicy,
} from "./useGatePolicies";

interface GatePolicyPanelProps {
  teamId: string | null;
  /** False for a grade that may read the policy but not change it. */
  canEdit: boolean;
}

interface Draft {
  epssOverridden: boolean;
  epss: string;
  reachableOverridden: boolean;
  reachable: boolean;
  maliciousOverridden: boolean;
  malicious: boolean;
  approvalOverridden: boolean;
  approval: string[];
}

const EMPTY_DRAFT: Draft = {
  epssOverridden: false,
  epss: "",
  reachableOverridden: false,
  reachable: false,
  maliciousOverridden: false,
  malicious: true,
  approvalOverridden: false,
  approval: [],
};

function draftFrom(
  policy: {
    epss_threshold: number | null;
    reachable_critical_only: boolean | null;
    malicious_blocks: boolean | null;
    approval_required_statuses: string[] | null;
  } | null,
): Draft {
  if (policy === null) return EMPTY_DRAFT;
  return {
    epssOverridden: policy.epss_threshold !== null,
    epss: policy.epss_threshold === null ? "" : String(policy.epss_threshold),
    reachableOverridden: policy.reachable_critical_only !== null,
    reachable: policy.reachable_critical_only ?? false,
    maliciousOverridden: policy.malicious_blocks !== null,
    malicious: policy.malicious_blocks ?? true,
    approvalOverridden: policy.approval_required_statuses !== null,
    approval: policy.approval_required_statuses ?? [],
  };
}

function payloadFrom(draft: Draft, name: string | null): GatePolicyUpsertIn {
  return {
    // The editor has no field for the name, so it sends back the one it read.
    // Leaving it out stored NULL, because the backend replaces the whole row.
    name,
    // Null, not omitted: the backend replaces the row wholesale, so a field
    // sent as null is how the team stops overriding it.
    epss_threshold: draft.epssOverridden && draft.epss !== "" ? Number(draft.epss) : null,
    reachable_critical_only: draft.reachableOverridden ? draft.reachable : null,
    malicious_blocks: draft.maliciousOverridden ? draft.malicious : null,
    approval_required_statuses: draft.approvalOverridden ? draft.approval : null,
  };
}

export function GatePolicyPanel({ teamId, canEdit }: GatePolicyPanelProps) {
  const { t } = useTranslation("policies");
  const query = useTeamGatePolicy(teamId);
  const upsert = useUpsertTeamGatePolicy();
  const remove = useDeleteTeamGatePolicy(teamId);
  // Deployment-scoped, so it is the same answer whichever team is open.
  const { data: epssData } = useEpssAvailability();
  // The draft remembers which team it was seeded for. Save replaces the
  // team's row wholesale, so a draft that was never seeded from a successful
  // read (still loading, read failed, or seeded for the previous team) must
  // never reach the mutation: it would overwrite the real policy with blanks.
  // The seed also keeps what the draft was built from: the row's name and its
  // entity tag. A save sends that tag back, so a save somebody else made after
  // this read is refused instead of overwritten.
  const [seeded, setSeeded] = useState<{
    teamId: string;
    draft: Draft;
    name: string | null;
    basis: GatePolicyBasis;
  } | null>(null);

  useEffect(() => {
    if (query.isSuccess && teamId !== null) {
      const read = query.data;
      setSeeded({
        teamId,
        draft: draftFrom(read?.policy ?? null),
        name: read?.policy.name ?? null,
        basis: { etag: read?.etag ?? null },
      });
    }
  }, [query.isSuccess, query.data, teamId]);

  if (teamId === null) return null;

  if (query.isError) {
    return (
      <section className="space-y-4" data-testid="gate-policy-panel">
        <Alert variant="destructive" data-testid="gate-policy-error">
          <AlertDescription className="space-y-2">
            <p>{t("gate.load_error")}</p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => void query.refetch()}
              data-testid="gate-policy-retry"
            >
              {t("gate.retry")}
            </Button>
          </AlertDescription>
        </Alert>
      </section>
    );
  }

  if (!query.isSuccess || seeded === null || seeded.teamId !== teamId) {
    return (
      <section className="space-y-3" data-testid="gate-policy-loading" aria-busy="true">
        <Skeleton className="h-5 w-1/3" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </section>
    );
  }

  const draft = seeded.draft;
  const setDraft = (update: (d: Draft) => Draft) =>
    setSeeded((s) => (s === null ? s : { ...s, draft: update(s.draft) }));

  const hasRow = query.data !== null && query.data !== undefined;
  const conflict = isGatePolicyConflict(upsert.error);
  // Reloading discards this draft on purpose: the row it was built from is
  // no longer the one on the server, and the edit has to be reapplied to what
  // the other person saved.
  const reload = () => {
    upsert.reset();
    void query.refetch();
  };
  const epssInvalid =
    draft.epssOverridden &&
    draft.epss !== "" &&
    (Number.isNaN(Number(draft.epss)) || Number(draft.epss) < 0 || Number(draft.epss) > 1);

  return (
    <section className="space-y-4" data-testid="gate-policy-panel">
      <div>
        <h3 className="text-sm font-semibold tracking-tight">{t("gate.title")}</h3>
        <p className="text-xs text-muted-foreground">{t("gate.description")}</p>
      </div>

      <div className="space-y-3">
        <div className="flex items-start gap-3">
          <Switch
            id="gate-epss-override"
            checked={draft.epssOverridden}
            disabled={!canEdit}
            onCheckedChange={(checked) =>
              setDraft((d) => ({ ...d, epssOverridden: checked }))
            }
            aria-label={t("gate.epss.override")}
            data-testid="gate-epss-override"
          />
          <div className="flex-1 space-y-1">
            <Label htmlFor="gate-epss" className="text-xs">
              {t("gate.epss.label")}
            </Label>
            <Input
              id="gate-epss"
              inputMode="decimal"
              value={draft.epss}
              disabled={!canEdit || !draft.epssOverridden}
              onChange={(e) => setDraft((d) => ({ ...d, epss: e.target.value }))}
              aria-invalid={epssInvalid || undefined}
              data-testid="gate-epss"
            />
            <p className="text-xs text-muted-foreground">
              {draft.epssOverridden ? t("gate.epss.help") : t("gate.inherited")}
            </p>
            {epssInvalid ? (
              <p className="text-xs text-destructive" role="alert">
                {t("gate.epss.range")}
              </p>
            ) : null}
            {/* ER43: a threshold set on a deployment that collects no EPSS
                decides nothing, and the builds it was meant to block pass. The
                person who sets it here is not the one reading CI output, so
                without this they never find out. */}
            {draft.epssOverridden && epssData?.available === false ? (
              <p
                className="text-xs text-muted-foreground"
                role="status"
                data-testid="gate-epss-unavailable"
              >
                {epssData.refresh_enabled
                  ? t("gate.epss.no_data_stale", {
                      count: epssData.scored_cves,
                    })
                  : t("gate.epss.no_data_disabled")}
              </p>
            ) : null}
          </div>
        </div>

        <div className="flex items-start gap-3">
          <Switch
            id="gate-reachable-override"
            checked={draft.reachableOverridden}
            disabled={!canEdit}
            onCheckedChange={(checked) =>
              setDraft((d) => ({ ...d, reachableOverridden: checked }))
            }
            aria-label={t("gate.reachable.override")}
            data-testid="gate-reachable-override"
          />
          <div className="flex-1 space-y-1">
            <Label htmlFor="gate-reachable" className="text-xs">
              {t("gate.reachable.label")}
            </Label>
            <Switch
              id="gate-reachable"
              checked={draft.reachable}
              disabled={!canEdit || !draft.reachableOverridden}
              onCheckedChange={(checked) => setDraft((d) => ({ ...d, reachable: checked }))}
              data-testid="gate-reachable"
            />
            <p className="text-xs text-muted-foreground">
              {draft.reachableOverridden ? t("gate.reachable.help") : t("gate.inherited")}
            </p>
          </div>
        </div>

        <div className="flex items-start gap-3">
          <Switch
            id="gate-malicious-override"
            checked={draft.maliciousOverridden}
            disabled={!canEdit}
            onCheckedChange={(checked) =>
              setDraft((d) => ({ ...d, maliciousOverridden: checked }))
            }
            aria-label={t("gate.malicious.override")}
            data-testid="gate-malicious-override"
          />
          <div className="flex-1 space-y-1">
            <Label htmlFor="gate-malicious" className="text-xs">
              {t("gate.malicious.label")}
            </Label>
            <Switch
              id="gate-malicious"
              checked={draft.malicious}
              disabled={!canEdit || !draft.maliciousOverridden}
              onCheckedChange={(checked) => setDraft((d) => ({ ...d, malicious: checked }))}
              data-testid="gate-malicious"
            />
            <p className="text-xs text-muted-foreground">
              {draft.maliciousOverridden ? t("gate.malicious.help") : t("gate.inherited")}
            </p>
          </div>
        </div>
      </div>

      <div className="flex items-start gap-3 border-t pt-4">
        <Switch
          id="gate-approval-override"
          checked={draft.approvalOverridden}
          disabled={!canEdit}
          onCheckedChange={(checked) =>
            setDraft((d) => ({ ...d, approvalOverridden: checked }))
          }
          aria-label={t("gate.approval.override")}
          data-testid="gate-approval-override"
        />
        <fieldset className="flex-1 space-y-1" disabled={!canEdit || !draft.approvalOverridden}>
          <legend className="text-xs font-medium">{t("gate.approval.label")}</legend>
          <p className="text-xs text-muted-foreground">
            {draft.approvalOverridden ? t("gate.approval.help") : t("gate.inherited")}
          </p>
          <div className="space-y-1 pt-1">
            {APPROVABLE_STATUSES.map((status) => (
              <label key={status} className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  className="size-3.5 accent-[var(--brand)]"
                  checked={draft.approval.includes(status)}
                  disabled={!canEdit || !draft.approvalOverridden}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      approval: e.target.checked
                        ? [...d.approval, status]
                        : d.approval.filter((s) => s !== status),
                    }))
                  }
                  data-testid={`gate-approval-${status}`}
                />
                {t(`gate.approval.status.${status}`)}
              </label>
            ))}
          </div>
          {draft.approvalOverridden && draft.approval.length > 0 ? (
            <p className="text-xs text-muted-foreground pt-1">
              {t("gate.approval.two_people_needed")}
            </p>
          ) : null}
        </fieldset>
      </div>

      {conflict ? (
        <Alert variant="destructive" data-testid="gate-policy-conflict">
          <AlertDescription className="space-y-2">
            <p>{t("gate.conflict")}</p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={reload}
              data-testid="gate-policy-conflict-reload"
            >
              {t("gate.conflict_reload")}
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}

      {canEdit ? (
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            disabled={epssInvalid || upsert.isPending}
            onClick={() =>
              upsert.mutate({
                teamId,
                payload: payloadFrom(draft, seeded.name),
                basis: seeded.basis,
              })
            }
            data-testid="gate-policy-save"
          >
            {t("gate.save")}
          </Button>
          {hasRow ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
              data-testid="gate-policy-reset"
            >
              {t("gate.reset")}
            </Button>
          ) : null}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground" data-testid="gate-policy-readonly">
          {t("gate.read_only")}
        </p>
      )}
    </section>
  );
}
