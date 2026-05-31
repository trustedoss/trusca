import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { TeamScopedRole } from "@/features/projects/api/projectDetailApi";
import {
  useUnwaiveLicense,
  useWaiveLicense,
  type LicenseException,
} from "@/features/projects/api/useLicenseWaive";
import { ProblemError } from "@/lib/problem";
import { cn } from "@/lib/utils";

/**
 * LicenseWaiveAction — per-component license waive control (Compliance tab).
 *
 * Renders next to one affected component of a forbidden (or otherwise gated)
 * license. Two states:
 *
 *   - NOT waived → a "Waive" button opens a {@link Dialog} asking for a
 *     mandatory reason + optional expiry, then POSTs a per-component exception.
 *   - Already waived → a "Waived" {@link Badge} (text label + colour, never
 *     colour alone) whose tooltip shows the reason, plus an "Un-waive" action
 *     that DELETEs the exception.
 *
 * Permission gate (CLAUDE.md §RBAC): only ``team_admin`` / ``super_admin`` may
 * waive. A ``developer`` sees the trigger disabled with a tooltip rather than
 * having it vanish — mirrors the VexImportDialog / suppression pattern. The
 * backend re-enforces the gate (403), so this is a UX affordance, not the
 * security boundary. When ``teamId`` is unknown (null) or the surface is a
 * read-only historical snapshot, the action is disabled too.
 *
 * Security: ``reason`` is rendered back only through React's default text
 * escaping (no ``dangerouslySetInnerHTML``). The purl is opaque to this
 * component — it is forwarded to the API verbatim.
 */

export interface LicenseWaiveActionProps {
  projectId: string;
  /** Owning team of the project. ``null`` when not yet resolved → disabled. */
  teamId: string | null;
  /** The actor's effective role within the project's owning team. */
  projectRole: TeamScopedRole;
  /** SPDX id of the license being waived (e.g. ``GPL-2.0-only``). */
  spdxId: string | null;
  /** Human label for the component (``name@version``) shown in the dialog. */
  componentLabel: string;
  /** purl_with_version the waiver is scoped to. ``null`` → cannot waive. */
  componentPurl: string | null;
  /** Existing exception for this (spdx, purl) pair, or null when not waived. */
  existing: LicenseException | null;
  /**
   * Whether an expiry is mandatory. A waiver on a FORBIDDEN license relaxes the
   * build gate, so the backend (``LICENSE_WAIVE_MAX_DAYS``) requires a capped
   * expiry and rejects an open-ended one with a 422. The dialog mirrors that
   * here: expiry becomes required and submit stays disabled until it is set.
   * Conditional / allowed waivers may be indefinite. Defaults to ``false``.
   */
  requireExpiry?: boolean;
  /** Read-only historical snapshot → waive/un-waive disabled. */
  readOnly?: boolean;
  className?: string;
}

export function LicenseWaiveAction({
  projectId,
  teamId,
  projectRole,
  spdxId,
  componentLabel,
  componentPurl,
  existing,
  requireExpiry = false,
  readOnly = false,
  className,
}: LicenseWaiveActionProps) {
  const { t } = useTranslation("project_detail");
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const reasonId = useId();
  const expiresId = useId();

  // The earliest selectable expiry — a past date is a dead-on-arrival waiver.
  const todayISO = new Date().toISOString().slice(0, 10);
  // Mirror the server's forbidden-license rule: reason is always required;
  // expiry is required too when this is a forbidden waiver.
  const missingRequired =
    reason.trim().length === 0 || (requireExpiry && expiresAt.length === 0);

  const waive = useWaiveLicense(projectId);
  const unwaive = useUnwaiveLicense(projectId);

  const roleAllows =
    projectRole === "team_admin" || projectRole === "super_admin";
  // A waiver needs a concrete team + purl to scope to. Without either we cannot
  // build a valid request, so the action is disabled regardless of role.
  const canWaive =
    roleAllows && !readOnly && teamId != null && componentPurl != null;

  const isWaived = existing != null;

  function resetForm() {
    setReason("");
    setExpiresAt("");
    waive.reset();
  }

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) resetForm();
  }

  function handleSubmit() {
    if (!canWaive || teamId == null || componentPurl == null) return;
    const trimmed = reason.trim();
    // reason (always) + expiry (forbidden waivers) are required — submit stays
    // disabled too, this is the belt-and-braces guard.
    if (trimmed.length === 0 || (requireExpiry && expiresAt.length === 0)) return;
    waive.mutate(
      {
        teamId,
        spdx_id: spdxId ?? "",
        reason: trimmed,
        component_purl: componentPurl,
        // Empty string → "never expires". A bare yyyy-mm-dd from <input
        // type=date> is widened to an ISO instant at UTC midnight so the
        // backend receives a valid RFC 3339 datetime.
        expires_at: expiresAt ? `${expiresAt}T00:00:00Z` : null,
      },
      {
        onSuccess: () => handleOpenChange(false),
      },
    );
  }

  function handleUnwaive() {
    if (!canWaive || teamId == null || componentPurl == null) return;
    unwaive.mutate({
      teamId,
      spdx_id: spdxId ?? "",
      component_purl: componentPurl,
    });
  }

  const gateTitle = readOnly
    ? t("snapshot.readonly_tooltip")
    : !roleAllows
      ? t("waive.role_gated")
      : teamId == null
        ? t("waive.no_team")
        : componentPurl == null
          ? t("waive.no_purl")
          : undefined;

  if (isWaived) {
    return (
      <span
        className={cn("inline-flex items-center gap-1.5", className)}
        data-testid="license-waive-state"
        data-waived="true"
        data-component-purl={componentPurl ?? ""}
        data-spdx-id={spdxId ?? ""}
      >
        <Badge
          tone="success"
          data-testid="license-waived-badge"
          // The reason is the durable record of WHY the gate was overridden —
          // expose it on hover so a reviewer can audit the waiver in place.
          title={t("waive.waived_tooltip", { reason: existing.reason })}
        >
          {t("waive.waived_badge")}
        </Badge>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          disabled={!canWaive || unwaive.isPending}
          onClick={handleUnwaive}
          title={gateTitle}
          data-testid="license-unwaive"
          data-role-gated={!canWaive ? "true" : undefined}
        >
          {unwaive.isPending
            ? t("waive.unwaiving")
            : t("waive.unwaive_button")}
        </Button>
        {unwaive.isError ? (
          <span
            className="text-xs text-destructive"
            aria-live="polite"
            data-testid="license-unwaive-error"
          >
            {mutationErrorMessage(unwaive.error, t)}
          </span>
        ) : null}
      </span>
    );
  }

  return (
    <span
      className={cn("inline-flex items-center", className)}
      data-testid="license-waive-state"
      data-waived="false"
      data-component-purl={componentPurl ?? ""}
      data-spdx-id={spdxId ?? ""}
    >
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 px-2 text-xs"
        disabled={!canWaive}
        onClick={() => handleOpenChange(true)}
        title={gateTitle}
        data-testid="license-waive-open"
        data-role-gated={!canWaive ? "true" : undefined}
        data-readonly-gated={readOnly ? "true" : undefined}
      >
        {t("waive.waive_button")}
      </Button>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent
          className="max-w-md"
          data-testid="license-waive-dialog"
          aria-describedby={`${reasonId}-desc`}
        >
          <DialogHeader>
            <DialogTitle>{t("waive.dialog_title")}</DialogTitle>
            <DialogDescription id={`${reasonId}-desc`}>
              {t("waive.dialog_description", {
                spdx_id: spdxId ?? t("compliance.row.no_spdx_id"),
                component: componentLabel,
              })}
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-3">
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
                data-testid="license-waive-expires"
                aria-required={requireExpiry ? "true" : undefined}
                className="h-9"
              />
              <p className="text-xs text-muted-foreground">
                {requireExpiry
                  ? t("waive.expires_required_hint")
                  : t("waive.expires_hint")}
              </p>
            </div>
          </div>

          {waive.isError ? (
            <Alert variant="destructive" data-testid="license-waive-error">
              <AlertDescription aria-live="polite">
                {mutationErrorMessage(waive.error, t)}
              </AlertDescription>
            </Alert>
          ) : null}

          <DialogFooter>
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
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </span>
  );
}

/**
 * Map a thrown waive/un-waive error to localized copy, branching on the RFC
 * 7807 status (403 permission / 422 malformed), else the server ``detail``.
 */
function mutationErrorMessage(
  error: Error | null,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  if (error instanceof ProblemError) {
    switch (error.status) {
      case 403:
        return t("waive.error_forbidden");
      case 422:
        // The server's governance message (e.g. "the waiver expiry … exceeds the
        // maximum of 90 days") is precise — surface it when present, else the
        // generic malformed-input copy.
        return error.detail || t("waive.error_malformed");
      default:
        return error.detail || t("waive.error_generic");
    }
  }
  return t("waive.error_generic");
}
