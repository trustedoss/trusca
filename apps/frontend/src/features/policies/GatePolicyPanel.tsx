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

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { APPROVABLE_STATUSES, type GatePolicyUpsertIn } from "@/lib/gatePoliciesApi";

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

function payloadFrom(draft: Draft): GatePolicyUpsertIn {
  return {
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
  const upsert = useUpsertTeamGatePolicy(teamId);
  const remove = useDeleteTeamGatePolicy(teamId);
  // Deployment-scoped, so it is the same answer whichever team is open.
  const { data: epssData } = useEpssAvailability();
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);

  useEffect(() => {
    if (query.isSuccess) setDraft(draftFrom(query.data));
  }, [query.isSuccess, query.data]);

  if (teamId === null) return null;

  const hasRow = query.data !== null && query.data !== undefined;
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
            data-testid="gate-reachable-override"
          />
          <div className="flex-1 space-y-1">
            <Label className="text-xs">{t("gate.reachable.label")}</Label>
            <Switch
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
            data-testid="gate-malicious-override"
          />
          <div className="flex-1 space-y-1">
            <Label className="text-xs">{t("gate.malicious.label")}</Label>
            <Switch
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

      {canEdit ? (
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            disabled={epssInvalid || upsert.isPending}
            onClick={() => upsert.mutate(payloadFrom(draft))}
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
