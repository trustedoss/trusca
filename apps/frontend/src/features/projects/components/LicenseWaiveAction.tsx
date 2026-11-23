// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { Radio } from "@primer/react";
import type { TFunction } from "i18next";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/primer/alert";
import { Badge } from "@/components/primer/badge";
import { Button } from "@/components/primer/Button";
import { PrimerDialog } from "@/components/primer/PrimerDialog";
import { Input } from "@/components/primer/Input";
import { Textarea } from "@/components/primer/Textarea";
import type { TeamScopedRole } from "@/features/projects/api/projectDetailApi";
import {
  useUnwaiveLicense,
  useWaiveLicense,
  type LicenseException,
} from "@/features/projects/api/useLicenseWaive";
import { ProblemError } from "@/lib/problem";
import { problemMessage } from "@/lib/problemMessage";
import { cn } from "@/lib/utils";

/**
 * LicenseWaiveAction: the one waive control a Compliance row carries.
 *
 * A forbidden license row can list several affected components, and each
 * component is waived on its own (the exception is scoped to a purl). The row
 * used to give every component its own button on a second line, which made a
 * forbidden row about three times the 40px compact height. Now the row has a
 * single "Waive..." button and the choice of component happens in the dialog:
 *
 *   - every target is listed with its state. A component that is not waived
 *     is a radio choice; one that is waived shows a "Waived" badge (label and
 *     colour, never colour alone) with the reason on hover, plus "Un-waive".
 *   - below the list, a mandatory reason and an expiry (required for a
 *     forbidden license, mirroring the server's ``LICENSE_WAIVE_MAX_DAYS``
 *     rule) POST the exception for the chosen component.
 *
 * Next to the button, a "{n} waived" badge keeps the row honest about what is
 * already overridden without opening the dialog; its tooltip lists each
 * waived component with its reason.
 *
 * Permission gate (CLAUDE.md RBAC): only ``group_admin`` / ``super_admin``
 * may waive. Anyone else sees the trigger disabled with a tooltip rather than
 * having it vanish, the same pattern as the VEX import. The backend enforces
 * the gate again (403), so this is a UX affordance, not the security
 * boundary. An unresolved team, a read-only snapshot or a row without a
 * purl-carrying component disables the trigger too.
 *
 * Security: ``reason`` is rendered back only through React's default text
 * escaping. The purl is opaque here and forwarded to the API verbatim.
 */

export interface LicenseWaiveTarget {
  /** Stable React key (the component version id). */
  key: string;
  /** Human label (``name@version``). */
  label: string;
  /** purl_with_version the waiver is scoped to. */
  purl: string;
  /** Existing exception for this (spdx, purl) pair, or null when not waived. */
  existing: LicenseException | null;
}

export interface LicenseWaiveActionProps {
  projectId: string;
  /** Owning team of the project. ``null`` when not yet resolved: disabled. */
  teamId: string | null;
  /** The actor's effective role within the project's owning team. */
  projectRole: TeamScopedRole;
  /** SPDX id of the license being waived (e.g. ``GPL-2.0-only``). */
  spdxId: string | null;
  /** Components of this license row that a waiver can be scoped to. */
  targets: LicenseWaiveTarget[];
  /**
   * Whether an expiry is mandatory. A waiver on a FORBIDDEN license relaxes
   * the build gate, so the backend requires a capped expiry and rejects an
   * open-ended one with a 422. Conditional / allowed waivers may be
   * indefinite. Defaults to ``false``.
   */
  requireExpiry?: boolean;
  /** Read-only historical snapshot: waive and un-waive disabled. */
  readOnly?: boolean;
  className?: string;
}

export function LicenseWaiveAction({
  projectId,
  teamId,
  projectRole,
  spdxId,
  targets,
  requireExpiry = false,
  readOnly = false,
  className,
}: LicenseWaiveActionProps) {
  const { t } = useTranslation("project_detail");
  const [open, setOpen] = useState(false);
  const [selectedPurl, setSelectedPurl] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const reasonId = useId();
  const expiresId = useId();
  const targetGroupName = useId();

  // The earliest selectable expiry. A past date is a dead-on-arrival waiver.
  const todayISO = new Date().toISOString().slice(0, 10);

  const waive = useWaiveLicense(projectId);
  const unwaive = useUnwaiveLicense(projectId);

  const roleAllows =
    projectRole === "group_admin" || projectRole === "super_admin";
  const canWaive =
    roleAllows && !readOnly && teamId != null && targets.length > 0;

  const waivedTargets = targets.filter((tg) => tg.existing != null);
  const openTargets = targets.filter((tg) => tg.existing == null);
  // The choice only counts while it still points at a component that can be
  // waived; a stale selection (waived since) falls back to nothing.
  const chosen =
    openTargets.find((tg) => tg.purl === selectedPurl) ?? null;

  const missingRequired =
    chosen == null ||
    reason.trim().length === 0 ||
    (requireExpiry && expiresAt.length === 0);

  function resetForm() {
    setReason("");
    setExpiresAt("");
    waive.reset();
    unwaive.reset();
  }

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (next) {
      // With one open component there is nothing to choose, so it is
      // preselected; with several the first is, and the list shows the rest.
      setSelectedPurl(openTargets[0]?.purl ?? null);
    } else {
      resetForm();
    }
  }

  function handleSubmit() {
    if (!canWaive || teamId == null || chosen == null) return;
    const trimmed = reason.trim();
    // Submit is disabled in these cases too; this is the second guard.
    if (trimmed.length === 0 || (requireExpiry && expiresAt.length === 0))
      return;
    waive.mutate(
      {
        teamId,
        spdx_id: spdxId ?? "",
        reason: trimmed,
        component_purl: chosen.purl,
        // Empty string means "never expires". A bare yyyy-mm-dd from <input
        // type=date> is widened to an ISO instant at UTC midnight so the
        // backend receives a valid RFC 3339 datetime.
        expires_at: expiresAt ? `${expiresAt}T00:00:00Z` : null,
      },
      {
        onSuccess: () => handleOpenChange(false),
      },
    );
  }

  function handleUnwaive(purl: string) {
    if (!canWaive || teamId == null) return;
    unwaive.mutate({
      teamId,
      spdx_id: spdxId ?? "",
      component_purl: purl,
    });
  }

  const gateTitle = readOnly
    ? t("snapshot.readonly_tooltip")
    : !roleAllows
      ? t("waive.role_gated")
      : teamId == null
        ? t("waive.no_team")
        : undefined;

  const waivedSummary = waivedTargets
    .map((tg) =>
      t("waive.waived_count_item", {
        component: tg.label,
        reason: tg.existing?.reason ?? "",
      }),
    )
    .join("\n");

  return (
    <span
      className={cn("inline-flex items-center gap-1.5", className)}
      data-testid="license-waive-control"
      data-waived-count={waivedTargets.length}
    >
      {waivedTargets.length > 0 ? (
        <Badge
          tone="success"
          data-testid="license-waived-count"
          // The reasons are the durable record of WHY the gate was
          // overridden, so a reviewer can audit them in place.
          title={waivedSummary}
        >
          {t("waive.waived_count", { count: waivedTargets.length })}
        </Badge>
      ) : null}
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={!canWaive}
        onClick={() => handleOpenChange(true)}
        title={gateTitle}
        aria-label={t("waive.waive_button_aria", {
          spdx_id: spdxId ?? t("compliance.row.no_spdx_id"),
        })}
        data-testid="license-waive-open"
        data-role-gated={!canWaive ? "true" : undefined}
        data-readonly-gated={readOnly ? "true" : undefined}
      >
        {t("waive.waive_button")}
      </Button>

      <PrimerDialog
        open={open}
        onOpenChange={handleOpenChange}
        title={t("waive.dialog_title")}
        description={t("waive.dialog_description", {
          spdx_id: spdxId ?? t("compliance.row.no_spdx_id"),
        })}
        width="large"
        testId="license-waive-dialog"
        footer={
          <>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => handleOpenChange(false)}
              data-testid="license-waive-cancel"
            >
              {t("waive.cancel")}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={missingRequired || waive.isPending}
              onClick={handleSubmit}
              data-testid="license-waive-submit"
            >
              {waive.isPending ? t("waive.waiving") : t("waive.submit")}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <fieldset
            className="flex flex-col gap-1.5"
            data-testid="license-waive-targets"
          >
            <legend className="mb-1.5 text-xs font-medium text-muted-foreground">
              {t("waive.target_label")}
            </legend>
            <ul className="flex max-h-56 flex-col overflow-y-auto rounded-md border">
              {targets.map((tg) => (
                <WaiveTargetRow
                  key={tg.key}
                  target={tg}
                  spdxId={spdxId}
                  groupName={targetGroupName}
                  checked={chosen?.purl === tg.purl}
                  onChoose={() => setSelectedPurl(tg.purl)}
                  onUnwaive={() => handleUnwaive(tg.purl)}
                  unwaiveDisabled={!canWaive || unwaive.isPending}
                  unwaiving={
                    unwaive.isPending &&
                    unwaive.variables?.component_purl === tg.purl
                  }
                />
              ))}
            </ul>
            {openTargets.length === 0 ? (
              <p
                className="text-xs text-muted-foreground"
                data-testid="license-waive-all-waived"
              >
                {t("waive.all_waived")}
              </p>
            ) : null}
            {unwaive.isError ? (
              <p
                className="text-xs text-destructive"
                aria-live="polite"
                data-testid="license-unwaive-error"
              >
                {mutationErrorMessage(unwaive.error, t)}
              </p>
            ) : null}
          </fieldset>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor={reasonId}
              className="text-xs font-medium text-muted-foreground"
            >
              {t("waive.reason_label")}
            </label>
            <Textarea
              id={reasonId}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("waive.reason_placeholder")}
              disabled={openTargets.length === 0}
              data-testid="license-waive-reason"
              aria-required="true"
              className="min-h-[80px] text-sm"
            />
            <p className="text-xs text-muted-foreground">
              {t("waive.reason_hint")}
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor={expiresId}
              className="text-xs font-medium text-muted-foreground"
            >
              {requireExpiry
                ? t("waive.expires_required_label")
                : t("waive.expires_label")}
            </label>
            <Input
              id={expiresId}
              type="date"
              value={expiresAt}
              min={todayISO}
              onChange={(e) => setExpiresAt(e.target.value)}
              disabled={openTargets.length === 0}
              data-testid="license-waive-expires"
              aria-required={requireExpiry ? "true" : undefined}
              className="h-8"
            />
            <p className="text-xs text-muted-foreground">
              {requireExpiry
                ? t("waive.expires_required_hint")
                : t("waive.expires_hint")}
            </p>
          </div>

          {waive.isError ? (
            <Alert variant="destructive" data-testid="license-waive-error">
              <AlertDescription aria-live="polite">
                {mutationErrorMessage(waive.error, t)}
              </AlertDescription>
            </Alert>
          ) : null}
        </div>
      </PrimerDialog>
    </span>
  );
}

interface WaiveTargetRowProps {
  target: LicenseWaiveTarget;
  spdxId: string | null;
  groupName: string;
  checked: boolean;
  onChoose: () => void;
  onUnwaive: () => void;
  unwaiveDisabled: boolean;
  unwaiving: boolean;
}

/** One component in the dialog's target list, in its waived or open state. */
function WaiveTargetRow({
  target,
  spdxId,
  groupName,
  checked,
  onChoose,
  onUnwaive,
  unwaiveDisabled,
  unwaiving,
}: WaiveTargetRowProps) {
  const { t } = useTranslation("project_detail");
  const radioId = useId();
  const isWaived = target.existing != null;
  return (
    <li
      className="flex min-h-10 items-center gap-2 border-b px-3 py-1 last:border-b-0"
      data-testid="compliance-waive-component"
      data-component-purl={target.purl}
    >
      <span
        className="flex min-w-0 flex-1 items-center gap-2"
        data-testid="license-waive-state"
        data-waived={isWaived ? "true" : "false"}
        data-component-purl={target.purl}
        data-spdx-id={spdxId ?? ""}
      >
        {isWaived ? (
          <>
            <span
              className="min-w-0 flex-1 truncate font-mono text-xs"
              title={target.purl}
            >
              {target.label}
            </span>
            <Badge
              tone="success"
              data-testid="license-waived-badge"
              title={t("waive.waived_tooltip", {
                reason: target.existing?.reason ?? "",
              })}
            >
              {t("waive.waived_badge")}
            </Badge>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={unwaiveDisabled}
              onClick={onUnwaive}
              data-testid="license-unwaive"
            >
              {unwaiving ? t("waive.unwaiving") : t("waive.unwaive_button")}
            </Button>
          </>
        ) : (
          <>
            <Radio
              id={radioId}
              name={groupName}
              value={target.purl}
              checked={checked}
              onChange={onChoose}
              data-testid="license-waive-target"
            />
            <label
              htmlFor={radioId}
              className="min-w-0 flex-1 cursor-pointer truncate font-mono text-xs"
              title={target.purl}
            >
              {target.label}
            </label>
          </>
        )}
      </span>
    </li>
  );
}

/**
 * Map a thrown waive/un-waive error to localized copy, branching on the RFC
 * 7807 status (403 permission), else the shared problem helper.
 */
function mutationErrorMessage(error: Error | null, t: TFunction): string {
  // 403 has its own wording here (waivers are a governance action, so "you
  // are not allowed to waive" beats the generic permission line). Everything
  // else, including the 422 whose detail names the governance limit, goes
  // through the shared helper: it keeps that detail and it stops a transport
  // failure from putting axios's "Network Error" on the screen.
  if (error instanceof ProblemError && error.status === 403) {
    return t("waive.error_forbidden");
  }
  return problemMessage(error, t, { action: "waive.error_generic" });
}
