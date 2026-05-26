import { useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useVexImport } from "@/features/projects/api/useVexImport";
import type {
  VexImportItemError,
  VexImportSummary,
} from "@/features/projects/api/vexApi";
import type { TeamScopedRole } from "@/features/projects/api/projectDetailApi";
import { ProblemError } from "@/lib/problem";
import { cn } from "@/lib/utils";

/**
 * VexImportDialog — v2.1 Track A (A3).
 *
 * Trigger button + modal for uploading a VEX document (OpenVEX / CycloneDX
 * VEX). The upload auto-transitions matching findings on the backend; the
 * dialog renders the returned summary (matched / applied / skipped + per-row
 * skip reasons) or a graceful RFC 7807 error (403 / 404 / 413 / 422).
 *
 * Permission gate: the *project-team-scoped* role must be `team_admin` (or
 * `super_admin`). For a `developer` the trigger renders disabled with a
 * tooltip — instead of vanishing — so a collaborating member understands the
 * action exists but is gated (matches the suppression-button pattern in
 * VulnerabilityDrawer). The backend re-enforces the gate (403), so this is a
 * UX affordance, not the security boundary.
 *
 * Security: the file is read by the browser only as a multipart body; we never
 * parse or render its contents here. Any text the import echoes back (error
 * `detail`, skipped vuln/product ids) is rendered through React's default text
 * escaping. `dangerouslySetInnerHTML` is never used.
 */

const ALL_SKIP_REASONS = new Set<VexImportItemError["reason"]>([
  "unknown_vulnerability",
  "unknown_component",
  "ambiguous_match",
  "unmapped_status",
  "illegal_transition",
  "already_at_target",
  "forbidden_transition",
  "malformed_statement",
]);

export interface VexImportDialogProps {
  projectId: string;
  /** The actor's effective role within the project's owning team. */
  projectRole?: TeamScopedRole;
  /**
   * Historical (read-only) snapshot mode (feature #28). When `true`, the import
   * trigger is disabled regardless of role — importing into an older snapshot
   * would mutate the *current* findings, which is wrong. The tooltip explains
   * the read-only state; it takes precedence over the role-gated tooltip.
   */
  readOnly?: boolean;
  className?: string;
}

export function VexImportDialog({
  projectId,
  projectRole = "developer",
  readOnly = false,
  className,
}: VexImportDialogProps) {
  const { t } = useTranslation("project_detail");
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [summary, setSummary] = useState<VexImportSummary | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileInputId = useId();

  const mutation = useVexImport(projectId);
  const roleAllowsImport =
    projectRole === "team_admin" || projectRole === "super_admin";
  // Read-only historical snapshot disables the import entirely (a higher
  // precedence gate than the role check).
  const canImport = roleAllowsImport && !readOnly;

  function reset() {
    setFile(null);
    setSummary(null);
    mutation.reset();
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) reset();
  }

  function handleSubmit() {
    if (!file) return;
    setSummary(null);
    mutation.mutate(
      { file },
      {
        onSuccess: (result) => {
          setSummary(result);
        },
      },
    );
  }

  return (
    <div className={cn("flex flex-col", className)}>
      <span className="text-xs font-medium text-muted-foreground">
        {t("vulnerabilities.vex.import_label")}
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="mt-1 h-9"
        disabled={!canImport}
        onClick={() => handleOpenChange(true)}
        data-testid="vex-import-open"
        data-role-gated={!canImport ? "true" : undefined}
        data-readonly-gated={readOnly ? "true" : undefined}
        title={
          readOnly
            ? t("snapshot.readonly_tooltip")
            : canImport
              ? undefined
              : t("vulnerabilities.vex.import_role_gated")
        }
      >
        {t("vulnerabilities.vex.import_button")}
      </Button>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent
          className="max-w-lg"
          data-testid="vex-import-dialog"
          aria-describedby={`${fileInputId}-desc`}
        >
          <DialogHeader>
            <DialogTitle>{t("vulnerabilities.vex.import_title")}</DialogTitle>
            <DialogDescription id={`${fileInputId}-desc`}>
              {t("vulnerabilities.vex.import_description")}
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-2">
            <label
              htmlFor={fileInputId}
              className="text-xs font-medium text-muted-foreground"
            >
              {t("vulnerabilities.vex.import_file_label")}
            </label>
            <input
              ref={fileInputRef}
              id={fileInputId}
              type="file"
              accept="application/json,.json"
              data-testid="vex-import-file"
              onChange={(e) => {
                setSummary(null);
                mutation.reset();
                setFile(e.target.files?.[0] ?? null);
              }}
              className="block w-full rounded-md border border-input bg-background text-sm file:mr-3 file:border-0 file:bg-muted file:px-3 file:py-2 file:text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <p
              id={`${fileInputId}-hint`}
              className="text-xs text-muted-foreground"
            >
              {t("vulnerabilities.vex.import_file_hint")}
            </p>
          </div>

          {mutation.isError ? (
            <Alert variant="destructive" data-testid="vex-import-error">
              <AlertDescription aria-live="polite">
                {importErrorMessage(mutation.error, t)}
              </AlertDescription>
            </Alert>
          ) : null}

          {summary ? (
            <VexImportSummaryPanel summary={summary} />
          ) : null}

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => handleOpenChange(false)}
              data-testid="vex-import-cancel"
            >
              {t("vulnerabilities.vex.import_close")}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!file || mutation.isPending}
              onClick={handleSubmit}
              data-testid="vex-import-submit"
            >
              {mutation.isPending
                ? t("vulnerabilities.vex.import_uploading")
                : t("vulnerabilities.vex.import_submit")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * Map a thrown import error to a localized message. We branch on the RFC 7807
 * status so the analyst gets an actionable hint (too large / malformed / not
 * permitted), falling back to the server `detail` for anything else.
 */
function importErrorMessage(
  error: Error | null,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  if (error instanceof ProblemError) {
    switch (error.status) {
      case 403:
        return t("vulnerabilities.vex.import_error_forbidden");
      case 404:
        return t("vulnerabilities.vex.import_error_not_found");
      case 413:
        return t("vulnerabilities.vex.import_error_too_large");
      case 422:
        return t("vulnerabilities.vex.import_error_malformed");
      default:
        // `detail` is server-supplied text — rendered as escaped text by React.
        return error.detail || t("vulnerabilities.vex.import_error_generic");
    }
  }
  return t("vulnerabilities.vex.import_error_generic");
}

interface SummaryPanelProps {
  summary: VexImportSummary;
}

function VexImportSummaryPanel({ summary }: SummaryPanelProps) {
  const { t } = useTranslation("project_detail");
  return (
    <div
      className="flex flex-col gap-3 rounded-md border p-3"
      data-testid="vex-import-summary"
      data-applied={summary.applied}
      data-matched={summary.matched}
      data-skipped={summary.skipped}
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="rounded-md border bg-muted/50 px-2 py-0.5 text-xs uppercase tracking-wide">
          {t(`vulnerabilities.vex.format_${summary.format}`)}
        </span>
      </div>
      <dl className="grid grid-cols-3 gap-2 text-center text-sm">
        <SummaryStat
          testId="vex-import-summary-matched"
          label={t("vulnerabilities.vex.summary_matched")}
          value={summary.matched}
        />
        <SummaryStat
          testId="vex-import-summary-applied"
          label={t("vulnerabilities.vex.summary_applied")}
          value={summary.applied}
        />
        <SummaryStat
          testId="vex-import-summary-skipped"
          label={t("vulnerabilities.vex.summary_skipped")}
          value={summary.skipped}
        />
      </dl>

      {summary.errors.length > 0 ? (
        <div className="flex flex-col gap-1">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t("vulnerabilities.vex.summary_errors_heading", {
              count: summary.errors.length,
            })}
          </h4>
          <ul
            className="flex max-h-48 flex-col gap-1 overflow-y-auto"
            data-testid="vex-import-summary-errors"
          >
            {summary.errors.map((err, idx) => (
              <li
                key={idx}
                data-testid="vex-import-summary-error-row"
                data-reason={err.reason}
                className="rounded border p-2 text-xs"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded bg-muted px-1.5 py-0.5 font-mono">
                    {ALL_SKIP_REASONS.has(err.reason)
                      ? t(`vulnerabilities.vex.skip_reason.${err.reason}`)
                      : err.reason}
                  </span>
                  {err.vulnerability ? (
                    <span className="font-mono text-muted-foreground">
                      {err.vulnerability}
                    </span>
                  ) : null}
                </div>
                {/* `detail`, `product`, `vulnerability` are document-supplied;
                    rendered as escaped text by React (no innerHTML). */}
                <p className="mt-1 text-muted-foreground">{err.detail}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

interface SummaryStatProps {
  testId: string;
  label: string;
  value: number;
}

function SummaryStat({ testId, label, value }: SummaryStatProps) {
  return (
    <div className="rounded-md border bg-muted/30 p-2">
      <div
        className="font-mono text-lg tabular-nums"
        data-testid={testId}
      >
        {value}
      </div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
    </div>
  );
}
