import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Loader2,
  XCircle,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { ScanCancelButton } from "@/features/scans/ScanCancelButton";
import { useScanWebSocket, type ScanStep } from "@/hooks/useScanWebSocket";
import { cn } from "@/lib/utils";
import type { ScanStatus } from "@/lib/projectsApi";

/**
 * ScanProgress — Phase 2 PR #9 task 2.10.
 *
 * Subscribes to /ws/scans/{scanId} and renders:
 *   - A bar with the current percent + step label.
 *   - A 7-step pipeline list (bootstrap → fetch → cdxgen → scancode → dt_upload
 *     → dt_findings → finalize) with a tick / spinner / waiting glyph per
 *     step.
 *   - "Reconnecting…" inline notice while the hook backs off.
 *   - Success / failure terminal panel + retry/close affordances.
 *
 * The component is presentational once the hook returns: tests can drive
 * every branch by stubbing the WebSocket constructor (see useScanWebSocket).
 */

export interface ScanProgressProps {
  scanId: string;
  /** Parent-controlled close (Sheet `onOpenChange={false}` etc.). */
  onClose?: () => void;
  /** Retry CTA shown on terminal failure. */
  onRetry?: () => void;
  /**
   * Surfaced when the project's vulnerability data was served from the cache
   * because Dependency-Track was unavailable. Phase 2 backend does not yet
   * publish this flag — kept here so the UI is wired and PR #10 can flip
   * a single boolean.
   */
  cachedFromDtDown?: boolean;
  /**
   * Persisted scan status (PR-A3). When `queued`/`running` and the live
   * WebSocket has not yet reported a terminal step, the panel shows a
   * "Cancel scan" affordance. Omit to hide cancellation entirely (e.g. a
   * read-only viewer). Defaults to `running`.
   */
  status?: ScanStatus;
  /** Fired after the backend confirms a user-initiated cancellation. */
  onCancelled?: () => void;
  /** Test seam — inject a custom WebSocket factory through to the hook. */
  socketFactory?: (url: string) => WebSocket;
  /** Test seam — override URL building. */
  urlBuilder?: (scanId: string) => string;
}

const PIPELINE_STEPS: ScanStep[] = [
  "bootstrap",
  "fetch",
  "cdxgen",
  "scancode",
  "dt_upload",
  "dt_findings",
  "finalize",
];

const TERMINAL_STEPS = new Set<ScanStep>(["succeeded", "failed"]);

function indexOfStep(step: ScanStep | null | undefined): number {
  if (!step) return -1;
  return PIPELINE_STEPS.indexOf(step as ScanStep);
}

export function ScanProgress({
  scanId,
  onClose,
  onRetry,
  cachedFromDtDown = false,
  status = "running",
  onCancelled,
  socketFactory,
  urlBuilder,
}: ScanProgressProps) {
  const { t } = useTranslation("scans");
  const { state, lastMessage, reconnectAttempt, isTerminal } =
    useScanWebSocket(scanId, { socketFactory, urlBuilder });

  const percent = lastMessage?.percent ?? 0;
  const step = lastMessage?.step ?? null;
  const succeeded = step === "succeeded";
  const failed = step === "failed";

  // The cancel affordance is gated on BOTH the persisted status (queued /
  // running) AND the absence of a live terminal frame — so it disappears the
  // instant the WebSocket reports success/failure even before the row refetch.
  const showCancel =
    !isTerminal && (status === "queued" || status === "running");

  return (
    <div className="flex flex-col gap-4" data-testid="scan-progress">
      <div className="flex items-baseline justify-between">
        <h2 className="text-base font-semibold tracking-tight">
          {succeeded
            ? t("progress.step_succeeded")
            : failed
              ? t("progress.step_failed")
              : t("progress.title")}
        </h2>
        <span
          className="font-mono text-xs text-muted-foreground"
          data-testid="scan-progress-percent"
        >
          {t("progress.percent_label", { value: percent })}
        </span>
      </div>

      {cachedFromDtDown ? (
        <Alert
          className="border-risk-medium/40 bg-risk-medium/5 text-risk-medium"
          data-testid="scan-dt-cached-alert"
        >
          <AlertTriangle className="h-4 w-4" aria-hidden />
          <AlertDescription>{t("alerts.dt_unavailable")}</AlertDescription>
        </Alert>
      ) : null}

      {state === "connecting" || state === "authenticating" ? (
        <Skeleton
          className="h-2 w-full"
          data-testid="scan-progress-skeleton"
        />
      ) : (
        <Progress
          value={percent}
          className={cn(
            failed && "bg-risk-critical/15",
            succeeded && "bg-emerald-100",
          )}
          indicatorClassName={cn(
            failed && "bg-risk-critical",
            succeeded && "bg-emerald-600",
          )}
          aria-label={t("progress.title")}
          data-testid="scan-progress-bar"
        />
      )}

      <ol
        className="grid grid-cols-1 gap-1 text-xs"
        data-testid="scan-progress-steps"
      >
        {PIPELINE_STEPS.map((s) => {
          const stepIndex = indexOfStep(step);
          const myIndex = indexOfStep(s);
          const isCurrent = step === s && !TERMINAL_STEPS.has(step as ScanStep);
          const isCompleted =
            (stepIndex > myIndex && stepIndex !== -1) || succeeded;
          const isFailedAtThisStep = failed && stepIndex === myIndex;
          return (
            <li
              key={s}
              className={cn(
                "flex items-center gap-2 rounded-md border px-2 py-1.5",
                isCurrent && "border-primary/40 bg-primary/5",
                isCompleted && "border-emerald-200 text-foreground",
                isFailedAtThisStep && "border-risk-critical/40 text-risk-critical",
              )}
              data-step={s}
              data-state={
                isCompleted
                  ? "completed"
                  : isCurrent
                    ? "current"
                    : isFailedAtThisStep
                      ? "failed"
                      : "pending"
              }
            >
              {isCompleted ? (
                <CheckCircle2
                  className="h-3.5 w-3.5 text-emerald-600"
                  aria-hidden
                />
              ) : isCurrent ? (
                <Loader2
                  className="h-3.5 w-3.5 animate-spin text-primary"
                  aria-hidden
                />
              ) : isFailedAtThisStep ? (
                <XCircle
                  className="h-3.5 w-3.5 text-risk-critical"
                  aria-hidden
                />
              ) : (
                <Circle
                  className="h-3.5 w-3.5 text-muted-foreground"
                  aria-hidden
                />
              )}
              <span>{t(`progress.step_${s}`, t("progress.step_unknown"))}</span>
            </li>
          );
        })}
      </ol>

      {!isTerminal && state !== "open" && reconnectAttempt > 0 ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="scan-progress-reconnecting"
          aria-live="polite"
        >
          {t("progress.reconnecting")}{" "}
          <span className="font-mono">
            {t("progress.reconnect_attempt", { n: reconnectAttempt })}
          </span>
        </p>
      ) : null}

      {!isTerminal ? (
        <p className="text-xs text-muted-foreground">
          {t("progress.background_notice")}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-end gap-2">
        {showCancel ? (
          <div className="mr-auto" data-testid="scan-progress-cancel-slot">
            <ScanCancelButton
              scanId={scanId}
              status={status}
              onCancelled={onCancelled}
            />
          </div>
        ) : null}
        {failed && onRetry ? (
          <Button
            variant="outline"
            size="sm"
            onClick={onRetry}
            data-testid="scan-progress-retry"
          >
            {t("progress.retry")}
          </Button>
        ) : null}
        {onClose ? (
          <Button
            variant={isTerminal ? "default" : "outline"}
            size="sm"
            onClick={onClose}
            data-testid="scan-progress-close"
          >
            {t("progress.close")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
