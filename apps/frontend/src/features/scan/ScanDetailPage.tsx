// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, Download, SearchX } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ScanProgress } from "@/features/scan/ScanProgress";
import { OsEolPanel } from "@/features/scan/OsEolPanel";
import { ScanProvenancePanel } from "@/features/scan/ScanProvenancePanel";
import { SbomConformancePanel } from "@/features/scan/SbomConformancePanel";
import { StreamStopped } from "@/features/scan/StreamStopped";
import { ToolLogLine } from "@/features/scan/ToolLogLine";
import { useSbomConformance } from "@/features/scan/useSbomConformance";
import { useScanProvenance } from "@/features/scan/useScanProvenance";
import {
  useScanWebSocket,
  type ScanLogMessage,
} from "@/hooks/useScanWebSocket";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { problemMessage } from "@/lib/problemMessage";
import { cn } from "@/lib/utils";
import { getScan, type ScanStatus } from "@/lib/projectsApi";

/**
 * ScanDetailPage — dedicated full-page surface for `/scans/:scanId`.
 *
 * Replaces the cramped log panel inside the right-side progress drawer with a
 * full-width route the user can deep-link, reload, and share. Layout:
 *
 *   ┌─────────────────────────────────────────────────────────┐
 *   │  Scan abcd1234 [running] [v1.2.3]    [Download log]    │  header
 *   ├─────────────────────────────────────────────────────────┤
 *   │  <ScanProgress hideInlineLog />                         │  progress
 *   │                                                         │
 *   │  Stage chips: All / cdxgen / scancode / trivy / Errors  │  filter
 *   │  ┌─────────────────────────────────────────────────────┐│
 *   │  │ 12:34:56  cdxgen  Generating SBOM…                  ││  log panel
 *   │  │ 12:34:57  trivy   err  Database refresh required    ││  (flex-1,
 *   │  │ …                                                   ││   scroll)
 *   │  └─────────────────────────────────────────────────────┘│
 *   └─────────────────────────────────────────────────────────┘
 *
 * Key behaviours:
 *   - Single unified stream from `useScanWebSocket.logMessages`. No per-stage
 *     tabs — a single-select chip row filters the same list in place.
 *   - Auto-scroll to the bottom on new messages, BUT pause when the user
 *     scrolls up. Detection: `scrollTop + clientHeight >= scrollHeight - 10`.
 *     Resumes when the user scrolls back to the bottom.
 *   - Download button calls `GET /api/v1/scans/{id}/log` (backend RFC 7807
 *     404 — existence-hide). Disabled while the scan is `queued` AND no log
 *     lines have streamed yet.
 *   - Uses the shared `ToolLogLine` so the row styling stays byte-for-byte
 *     consistent with the drawer's (now-hidden) inline panel.
 */

type LogFilter = "all" | "cdxgen" | "scancode" | "scanoss" | "trivy" | "errors";

const FILTER_CHIPS: { value: LogFilter; key: string }[] = [
  { value: "all", key: "detail.filter_all" },
  { value: "cdxgen", key: "progress.step_cdxgen" },
  { value: "scancode", key: "progress.step_scancode" },
  { value: "scanoss", key: "progress.step_scanoss" },
  { value: "trivy", key: "progress.step_trivy" },
  { value: "errors", key: "detail.filter_errors" },
];

interface DownloadToast {
  id: number;
  text: string;
  variant: "destructive" | "default";
}

function statusBadgeTone(
  status: ScanStatus | null | undefined,
): "info" | "low" | "success" | "critical" {
  switch (status) {
    case "running":
      return "low";
    case "succeeded":
      return "success";
    case "failed":
      return "critical";
    case "cancelled":
    case "queued":
    default:
      return "info";
  }
}

export function ScanDetailPage() {
  const { t } = useTranslation("scans");
  const { scanId } = useParams<{ scanId: string }>();

  // ---- Scan summary (single fetch, no realtime). The WebSocket below carries
  // the live progress; this query gives us the persisted status + release for
  // the header chrome and the download button gating.
  const scanQuery = useQuery({
    queryKey: ["scans", scanId, "detail"],
    queryFn: () => getScan(scanId as string),
    enabled: typeof scanId === "string" && scanId.length > 0,
    // Refetch only if the WS reports a terminal frame and the cached status
    // still says queued/running — TanStack handles this implicitly because
    // the WS hook publishes via React state and the consumer invalidates.
    staleTime: 30_000,
  });

  const scan = scanQuery.data;
  const liveStatus: ScanStatus | undefined = scan?.status;

  // ---- Received-SBOM conformance (model 3). Only meaningful for `kind: "sbom"`
  // ingests; the hook stays dormant otherwise. A 404 (no verdict yet /
  // unreachable) is swallowed by the hook (retry:false) and we simply render no
  // panel — same quiet-degrade posture as the rest of this page.
  const conformanceQuery = useSbomConformance(scan?.project_id, scanId, {
    enabled: scan?.kind === "sbom",
  });
  // Provenance is written as the scan runs and never changes after it ends, so
  // it is only asked for once the scan has finished (gap #31).
  const provenanceQuery = useScanProvenance(scanId, {
    enabled: scan?.status === "succeeded",
  });

  // ---- Live log stream. We pass through the existing hook so reconnection,
  // ring buffer, and the auth handshake are all reused.
  //
  // #137: this is the page's SECOND socket. The `ScanProgress` above holds the
  // other one, and the per-user cap (W4: 8, shared across every backend
  // process rather than counted per worker) can still evict either of them
  // with 1001 once enough tabs are open. When it takes this one the log
  // panel simply stops receiving lines, which is why it reports its own
  // give-up rather than borrowing the progress panel's.
  const {
    lastMessage,
    logMessages,
    gaveUp: logStreamGaveUp,
    closeCode: logStreamCloseCode,
    reconnect: reconnectLogStream,
    isTerminal: logStreamSawTerminal,
  } = useScanWebSocket(scanId ?? "", {
    enabled: typeof scanId === "string" && scanId.length > 0,
  });

  // A finished scan sends no more lines, so a closed socket is the expected
  // end of the stream and not something to report. Union of the two things
  // that can tell us: a terminal frame on this socket, or the persisted status
  // (which is what a reader who opens an old scan lands on).
  const scanFinished =
    logStreamSawTerminal ||
    liveStatus === "succeeded" ||
    liveStatus === "failed" ||
    liveStatus === "cancelled";

  // ---- Log filter (single-select chip row above the list).
  const [filter, setFilter] = useState<LogFilter>("all");

  const filteredMessages = useMemo<ScanLogMessage[]>(() => {
    if (filter === "all") return logMessages;
    if (filter === "errors") {
      return logMessages.filter((m) => m.stream === "stderr");
    }
    return logMessages.filter((m) => m.stage === filter);
  }, [logMessages, filter]);

  // ---- Auto-scroll: stick to the bottom while the user is already there.
  // The `pinnedToBottom` flag flips when the user scrolls up and back; the
  // effect below only scrolls when it's true. Detection threshold of 10 px
  // tolerates sub-pixel rounding when the body has many wrapped lines.
  const scrollRef = useRef<HTMLDivElement>(null);
  const [pinnedToBottom, setPinnedToBottom] = useState(true);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 10;
    setPinnedToBottom(atBottom);
  }, []);

  useEffect(() => {
    if (!pinnedToBottom) return;
    const el = scrollRef.current;
    if (!el) return;
    // rAF so the layout pass finishes before we read scrollHeight.
    const id = requestAnimationFrame(() => {
      el.scrollTop = el.scrollHeight;
    });
    return () => cancelAnimationFrame(id);
  }, [filteredMessages.length, pinnedToBottom, filter]);

  // ---- Download log. Disabled until either (a) we have streamed at least
  // one line in this session, (b) the persisted status has moved past
  // `queued` (the worker has started writing the on-disk log), or (c) this
  // page's own socket has observed a real step on its initial sync frame.
  //
  // (c) matters because this page opens a SECOND, later socket than the
  // drawer's (see the comment above) and the backend sends no log-line
  // backlog on connect, so a scan that finished emitting its early lines
  // before this socket attached would otherwise never satisfy (a). Nothing
  // ever invalidates `scanQuery` either (despite what its own comment
  // claims), so (b) can stay frozen at the `queued` snapshot from page load.
  // The initial sync frame is re-derived from the DB at THIS connection's
  // admission time, though, so `lastMessage.step` (`""` while queued, per
  // `build_progress_frame`) is the freshest signal available without
  // touching the backend or adding a poll.
  const [downloading, setDownloading] = useState(false);
  const [toast, setToast] = useState<DownloadToast | null>(null);
  const toastSeq = useRef(0);

  function notify(text: string, variant: "destructive" | "default" = "default") {
    toastSeq.current += 1;
    setToast({ id: toastSeq.current, text, variant });
  }

  const wsObservedStart = lastMessage !== null && lastMessage.step !== "";

  const downloadDisabled =
    !scanId ||
    downloading ||
    (liveStatus === "queued" && !wsObservedStart && logMessages.length === 0);

  const handleDownload = useCallback(async () => {
    if (!scanId) return;
    setDownloading(true);
    try {
      const res = await fetch(`/api/v1/scans/${scanId}/log`, {
        credentials: "include",
      });
      if (res.status === 404) {
        notify(t("detail.download_unavailable"), "destructive");
        return;
      }
      if (!res.ok) {
        notify(t("detail.download_unavailable"), "destructive");
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `scan-${scanId}.log`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      // Network / CORS / parse — surface the same neutral message; the
      // backend uniformly returns 404 for not-yet-written cases (existence-
      // hide), so a generic "not available yet" message stays accurate.
      notify(t("detail.download_unavailable"), "destructive");
    } finally {
      setDownloading(false);
    }
  }, [scanId, t]);

  // ---- Render guards.
  if (!scanId) {
    return (
      <div className="p-6" data-testid="scan-detail-page-missing-id">
        <Alert variant="destructive">
          <AlertDescription>
            {t("page.error", { defaultValue: "Could not load scans." })}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const shortId = scanId.slice(0, 8);
  const projectId = scan?.project_id ?? null;
  const projectHref = projectId ? `/projects/${projectId}` : "/projects";

  return (
    <div
      className="flex h-[calc(100vh-48px)] min-h-0 flex-col bg-background text-foreground"
      data-testid="scan-detail-page"
      data-scan-id={scanId}
    >
      <PageHeader
        scanId={scanId}
        shortId={shortId}
        status={liveStatus}
        release={scan?.release ?? null}
        projectName={scan?.project_name ?? null}
        projectHref={projectHref}
        onDownload={handleDownload}
        downloadDisabled={downloadDisabled}
        downloading={downloading}
      />

      <main className="mx-auto flex w-full max-w-[1440px] flex-1 flex-col gap-4 overflow-hidden px-6 py-4">
        {scanQuery.isLoading ? (
          <div
            className="flex flex-col gap-3"
            data-testid="scan-detail-page-loading"
          >
            <Skeleton className="h-6 w-1/3" />
            <Skeleton className="h-2 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : null}

        {/* A4: every failure here used to render one not-found sentence, so
            a 500 or a dropped connection accused an address that was fine,
            and the user's next move (retry) was the one the message argued
            against. The class decides the sentence now.

            `detail.errors` names the three classes this route can answer
            with: 404, 403, and the 422 FastAPI raises when the id in the path
            is not a UUID. Everything else resolves to the shared `common:`
            wording, and `allowDetailFallback: false` keeps the backend's
            English `detail` off a surface that is the whole page: a generic
            Korean sentence beats a specific English one here.

            (This was the last consumer of `scans:close_codes.*`, which named
            WebSocket close reasons but was being used for a REST error. Those
            four keys are gone: unit C4 found three of them untrue and replaced
            them with `scans:stream_stopped.*`, which the scan stream reports
            its own close reasons through.) */}
        {scanQuery.isError ? (
          <EmptyState
            data-testid="scan-detail-page-error"
            icon={<SearchX />}
            title={problemMessage(scanQuery.error, t, {
              prefix: "detail.errors",
              allowDetailFallback: false,
            })}
            action={
              <Button asChild variant="outline" size="sm">
                <Link to="/scans" data-testid="scan-detail-page-error-back">
                  {t("detail.back_to_scans")}
                </Link>
              </Button>
            }
          />
        ) : null}

        {scan ? (
          <>
            <section
              className="rounded-md border bg-card p-4 shadow-sm"
              data-testid="scan-detail-page-progress"
            >
              <ScanProgress
                scanId={scanId}
                release={scan.release}
                status={scan.status}
                hideInlineLog
              />
            </section>

            {scan.kind === "sbom" && conformanceQuery.data ? (
              <SbomConformancePanel conformance={conformanceQuery.data} />
            ) : null}

            {scan.kind === "container" ? (
              <OsEolPanel metadata={scan.metadata} />
            ) : null}

            {/* Renders nothing when neither half was recorded — an older scan
                has no story here and an empty card would be noise. */}
            <ScanProvenancePanel provenance={provenanceQuery.data} />

            <section
              className="flex min-h-0 flex-1 flex-col gap-2"
              data-testid="scan-detail-page-log-section"
            >
              <div
                className="flex flex-wrap items-center gap-2"
                role="group"
                aria-label={t("detail.filter_all")}
                data-testid="scan-detail-page-filters"
              >
                {FILTER_CHIPS.map((chip) => {
                  const active = filter === chip.value;
                  return (
                    <button
                      key={chip.value}
                      type="button"
                      onClick={() => setFilter(chip.value)}
                      data-testid={`scan-detail-page-filter-${chip.value}`}
                      data-active={active ? "true" : "false"}
                      aria-pressed={active}
                      className={cn(
                        "inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium transition-colors duration-fast ease-out-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                        active
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-border bg-card text-foreground hover:bg-muted",
                      )}
                    >
                      {t(chip.key)}
                    </button>
                  );
                })}
                <span
                  className="ml-auto font-mono text-[11px] tabular-nums text-muted-foreground"
                  data-testid="scan-detail-page-log-count"
                >
                  {filteredMessages.length}
                  {filter !== "all" || logMessages.length === filteredMessages.length
                    ? ""
                    : ` / ${logMessages.length}`}
                </span>
              </div>

              {/* #137 - above the box, not inside it, so it survives the
                  filter chips emptying the list and stays put while the
                  reader scrolls the lines that did arrive. */}
              {!scanFinished && logStreamGaveUp ? (
                <StreamStopped
                  surface="log"
                  closeCode={logStreamCloseCode}
                  onReconnect={reconnectLogStream}
                />
              ) : null}

              <div
                ref={scrollRef}
                onScroll={handleScroll}
                className="flex-1 overflow-y-auto rounded-md border bg-muted/30 font-mono text-[11px] leading-snug"
                data-testid="scan-detail-page-log"
                data-pinned-bottom={pinnedToBottom ? "true" : "false"}
                role="log"
                aria-live="polite"
                aria-relevant="additions"
              >
                {filteredMessages.length === 0 ? (
                  <div
                    className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground"
                    data-testid="scan-detail-page-log-empty"
                  >
                    {t("detail.empty")}
                  </div>
                ) : (
                  <ol data-testid="scan-detail-page-log-body">
                    {filteredMessages.map((msg, idx) => (
                      <ToolLogLine
                        key={`${msg.ts}-${idx}`}
                        msg={msg}
                      />
                    ))}
                  </ol>
                )}
              </div>
            </section>
          </>
        ) : null}
      </main>

      {toast ? (
        <div
          key={toast.id}
          className={cn(
            "fixed bottom-4 right-4 z-50 max-w-sm",
            "animate-in fade-in-0 slide-in-from-bottom-2 duration-base ease-out-soft",
          )}
          data-testid="scan-detail-page-toast"
          data-toast-variant={toast.variant}
        >
          <Alert
            variant={toast.variant === "destructive" ? "destructive" : "default"}
            className="shadow-lg"
            role="status"
            aria-live="polite"
          >
            <AlertDescription>{toast.text}</AlertDescription>
          </Alert>
        </div>
      ) : null}
    </div>
  );
}

interface PageHeaderProps {
  scanId: string;
  shortId: string;
  status: ScanStatus | undefined;
  release: string | null;
  projectName: string | null;
  projectHref: string;
  onDownload: () => void;
  downloadDisabled: boolean;
  downloading: boolean;
}

function PageHeader({
  scanId: _scanId,
  shortId,
  status,
  release,
  projectName,
  projectHref,
  onDownload,
  downloadDisabled,
  downloading,
}: PageHeaderProps) {
  const { t } = useTranslation("scans");

  useDocumentTitle(t("detail.title", { shortId }), projectName);

  return (
    <header
      className="flex flex-col gap-2 border-b px-6 py-3"
      data-testid="scan-detail-page-header"
    >
      <nav
        className="flex items-center gap-2 text-xs text-muted-foreground"
        aria-label="Breadcrumb"
      >
        <Link
          to="/scans"
          className="transition-colors duration-fast ease-out-soft hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="scan-detail-page-breadcrumb-scans"
        >
          {t("page.title")}
        </Link>
        <span aria-hidden>/</span>
        {projectName ? (
          <>
            <Link
              to={projectHref}
              className="transition-colors duration-fast ease-out-soft hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              data-testid="scan-detail-page-breadcrumb-project"
            >
              {projectName}
            </Link>
            <span aria-hidden>/</span>
          </>
        ) : null}
        <span
          className="font-mono"
          data-testid="scan-detail-page-breadcrumb-current"
        >
          {shortId}
        </span>
      </nav>

      <div className="flex flex-wrap items-center gap-3">
        <Link
          to={projectHref}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors duration-fast ease-out-soft hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          data-testid="scan-detail-page-back-link"
        >
          <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
          {t("detail.back_to_project")}
        </Link>

        <h1
          className="font-mono text-base font-semibold tracking-tight"
          data-testid="scan-detail-page-title"
        >
          {t("detail.title", { shortId })}
        </h1>

        {status ? (
          <Badge
            tone={statusBadgeTone(status)}
            variant="outline"
            data-testid="scan-detail-page-status"
          >
            {t(`page.status.${status}`)}
          </Badge>
        ) : null}

        {release ? (
          <span
            className="inline-flex shrink-0 items-center rounded-sm border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px] font-medium text-foreground"
            data-testid="scan-detail-page-release"
            aria-label={t("release.chip_aria", { release })}
            title={t("release.chip_aria", { release })}
          >
            {release}
          </span>
        ) : null}

        <div className="ml-auto">
          <Button
            type="button"
            variant="default"
            size="sm"
            onClick={onDownload}
            disabled={downloadDisabled}
            data-testid="scan-detail-page-download"
          >
            <Download className="mr-1 h-3.5 w-3.5" aria-hidden />
            {downloading
              ? `${t("detail.download")}…`
              : t("detail.download")}
          </Button>
        </div>
      </div>
    </header>
  );
}
