import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Circle,
  Loader2,
  XCircle,
} from "lucide-react";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { ScanCancelButton } from "@/features/scans/ScanCancelButton";
import { useScanWebSocket, type ScanStep } from "@/hooks/useScanWebSocket";
import { cn } from "@/lib/utils";
import { getScan, type ScanStatus } from "@/lib/projectsApi";

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
  /**
   * Optional release/version label this scan was triggered against (feature
   * #18), e.g. `v1.2.3`. When present, a small monospace chip renders in the
   * panel header (JetBrains Mono per the design system). Omit/null to hide.
   */
  release?: string | null;
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

const TERMINAL_STEPS = new Set<ScanStep>(["succeeded", "failed", "cancelled"]);

function indexOfStep(step: ScanStep | null | undefined): number {
  if (!step) return -1;
  return PIPELINE_STEPS.indexOf(step as ScanStep);
}

export function ScanProgress({
  scanId,
  release = null,
  onClose,
  onRetry,
  cachedFromDtDown = false,
  status = "running",
  onCancelled,
  socketFactory,
  urlBuilder,
}: ScanProgressProps) {
  const { t } = useTranslation("scans");

  // BUG-007 fallback: the backend cancel path closes the WebSocket without
  // publishing a `cancelled` frame, so a non-terminal close arms a one-shot
  // refetch of the scan status. When that comes back `cancelled` the panel
  // flips to the cancelled terminal state instead of leaving the bar stuck.
  const [closedNonTerminally, setClosedNonTerminally] = useState(false);
  const handleNonTerminalClose = useCallback(() => {
    setClosedNonTerminally(true);
  }, []);

  const { state, lastMessage, messages, reconnectAttempt, isTerminal } =
    useScanWebSocket(scanId, {
      socketFactory,
      urlBuilder,
      onNonTerminalClose: handleNonTerminalClose,
    });

  // P2 #8b — collapsible per-step log panel. Default collapsed; the headline
  // panel already shows percent + step + the per-step pipeline list. The
  // log is the deep-dive for users debugging a slow / stuck scan.
  const [logOpen, setLogOpen] = useState(false);

  // One-shot status refetch — only enabled after a non-terminal socket close
  // and only while the live stream has not already reported a terminal step.
  const fallbackQuery = useQuery({
    queryKey: ["scans", scanId, "status-fallback"],
    queryFn: () => getScan(scanId),
    enabled: closedNonTerminally && !isTerminal,
    staleTime: 0,
    retry: false,
  });

  const fetchedStatus = fallbackQuery.data?.status ?? null;

  const percent = lastMessage?.percent ?? 0;
  const step = lastMessage?.step ?? null;
  // P1 #11 — derive each terminal verdict from the *union* of (live WS frame,
  // parent-supplied status prop, fallback refetch). Re-opening a completed
  // scan's drawer (RecentScansTable row click) lands here with status =
  // "succeeded" but the live frame can transiently report step = "finalize"
  // (the worker's last `current_step` write before flipping status). Treating
  // `status` as a peer of `step` keeps the panel from rendering a spinner on
  // a scan that is in fact done.
  const succeeded =
    step === "succeeded" ||
    status === "succeeded" ||
    fetchedStatus === "succeeded";
  const failed =
    step === "failed" || status === "failed" || fetchedStatus === "failed";
  // A scan is cancelled when ANY of: the live frame says so, the parent passed
  // a cancelled status (the cancel button confirmed), or the fallback refetch
  // resolved to cancelled.
  const cancelled =
    step === "cancelled" ||
    status === "cancelled" ||
    fetchedStatus === "cancelled";

  // Treat any terminal verdict as terminal for the panel even when the WS
  // hook itself never saw a terminal frame (status prop / fallback path).
  const terminal = isTerminal || cancelled || succeeded || failed;

  // The cancel affordance is gated on BOTH the persisted status (queued /
  // running) AND the absence of a live terminal frame — so it disappears the
  // instant the WebSocket reports success/failure/cancellation even before the
  // row refetch.
  const showCancel =
    !terminal && (status === "queued" || status === "running");

  return (
    <div className="flex flex-col gap-4" data-testid="scan-progress">
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold tracking-tight">
            {succeeded
              ? t("progress.step_succeeded")
              : failed
                ? t("progress.step_failed")
                : cancelled
                  ? t("progress.step_cancelled")
                  : t("progress.title")}
          </h2>
          {release ? (
            <span
              className="inline-flex shrink-0 items-center rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px] font-medium text-foreground"
              data-testid="scan-progress-release"
              title={t("release.chip_aria", { release })}
              aria-label={t("release.chip_aria", { release })}
            >
              {release}
            </span>
          ) : null}
        </div>
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

      {(state === "connecting" || state === "authenticating") && !terminal ? (
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
            cancelled && "bg-muted",
          )}
          indicatorClassName={cn(
            failed && "bg-risk-critical",
            succeeded && "bg-emerald-600",
            // Cancelled = stopped. Tint the (frozen) bar the neutral
            // info/muted tone so it never reads as either success or failure.
            cancelled && "bg-muted-foreground",
          )}
          aria-label={t("progress.title")}
          data-testid="scan-progress-bar"
          data-cancelled={cancelled ? "true" : undefined}
        />
      )}

      {cancelled ? (
        <Alert
          className="border-muted-foreground/30 bg-muted/40 text-foreground"
          data-testid="scan-progress-cancelled"
          role="status"
          aria-live="polite"
        >
          <Ban className="h-4 w-4" aria-hidden />
          <AlertDescription>{t("progress.cancelled_notice")}</AlertDescription>
        </Alert>
      ) : null}

      <ol
        className="grid grid-cols-1 gap-1 text-xs"
        data-testid="scan-progress-steps"
      >
        {PIPELINE_STEPS.map((s) => {
          const stepIndex = indexOfStep(step);
          const myIndex = indexOfStep(s);
          // P1 #11 — once the scan reached any terminal verdict (cancelled /
          // succeeded / failed), freeze the per-step spinner: the in-flight
          // step is no longer "current" (work has stopped), so it renders as
          // a neutral / completed glyph rather than an animated loader. The
          // earlier guard only covered the cancelled branch, which left a
          // re-opened succeeded scan stuck spinning on `finalize`.
          const isCurrent =
            step === s &&
            !cancelled &&
            !succeeded &&
            !failed &&
            !TERMINAL_STEPS.has(step as ScanStep);
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

      {!terminal && state !== "open" && reconnectAttempt > 0 ? (
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

      {!terminal ? (
        <p className="text-xs text-muted-foreground">
          {t("progress.background_notice")}
        </p>
      ) : null}

      {/* P2 #8b — per-step log panel. Collapsed by default; the headline
          summary above is enough for the common case. When expanded the
          panel renders every frame the WebSocket has delivered (capped at
          500 in the hook) so users can see when each step started, how
          long it took, and whether a step bounced between percents while
          the worker was retrying. */}
      {messages.length > 0 ? (
        <div
          className="rounded-md border bg-muted/30"
          data-testid="scan-progress-log"
          data-open={logOpen ? "true" : "false"}
        >
          <button
            type="button"
            onClick={() => setLogOpen((v) => !v)}
            aria-expanded={logOpen}
            aria-controls="scan-progress-log-body"
            className="flex w-full items-center justify-between gap-2 px-3 py-2 text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            data-testid="scan-progress-log-toggle"
          >
            <span>
              {t("progress.log_toggle", {
                defaultValue: "Per-step log",
              })}
              <span
                className="ml-2 inline-flex items-center rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] tabular-nums"
                aria-hidden
              >
                {messages.length}
              </span>
            </span>
            <span aria-hidden className="font-mono text-[10px]">
              {logOpen ? "▼" : "▶"}
            </span>
          </button>
          {logOpen ? (
            <ol
              id="scan-progress-log-body"
              className="max-h-48 overflow-y-auto border-t font-mono text-[11px] leading-snug"
              data-testid="scan-progress-log-body"
            >
              {messages.map((msg, idx) => (
                <li
                  key={`${msg.ts}-${idx}`}
                  className="flex items-baseline gap-2 border-b px-3 py-1 last:border-b-0"
                  data-step={msg.step}
                >
                  <span
                    className="shrink-0 text-muted-foreground"
                    title={msg.ts}
                  >
                    {msg.ts.slice(11, 19)}
                  </span>
                  <span className="shrink-0 tabular-nums text-foreground">
                    {String(msg.percent).padStart(3, " ")}%
                  </span>
                  <span className="truncate text-foreground">
                    {msg.step || "—"}
                  </span>
                </li>
              ))}
            </ol>
          ) : null}
        </div>
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
            variant={terminal ? "default" : "outline"}
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
