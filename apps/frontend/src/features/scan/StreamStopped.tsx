// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * StreamStopped: what a surface says once its scan socket has stopped for
 * good (C4, extended to the log panel by #137).
 *
 * Before C4 the progress panel went on saying "Reconnecting… Attempt 14" for
 * as long as the page stayed open, because the hook's give-up was invisible
 * to it. Two things a reader needs at that moment: why it stopped, and
 * whether their scan died with it. It did not, and that sentence is here
 * every time it is true.
 *
 * The reason is keyed off the close code, and each string was written against
 * the line in the backend that sends that code. Codes the server cannot send
 * are not given server-voiced copy: 1006 in particular is the browser's own
 * "no close frame arrived", which is what a dropped network looks like.
 *
 * `/scans/:id` opens two of these sockets, one for the progress panel and one
 * for the log panel, and the per-user cap can evict either. `surface` decides
 * whose silence is being reported, because the consequence differs: a frozen
 * progress panel means the status may be out of date, a frozen log panel
 * means the lines end where the stream did.
 *
 * W4: the per-user cap now lives in a Redis-backed registry shared by every
 * backend process (was a per-worker-process dict), and a NEW global cap sits
 * beside it. The two read differently to a reader, which is why they map to
 * two different reasons below: an eviction (1001) is about THIS account's
 * own connections, while a capacity refusal (4429) is about the whole
 * deployment being full and has nothing to do with how many tabs this reader
 * has open.
 */
import { PlugZap, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

/**
 * Close code to the sentence that is true of it.
 *
 * Traced to `apps/backend/api/v1/ws.py`, the only file in the backend that
 * closes this socket:
 *   1001 `newer_connection` (W4) - the per-user connection cap evicted the
 *        oldest socket. An open scan page holds two of the eight allowed, so
 *        four or more tabs can take an earlier tab's stream. The count is
 *        shared by every backend process (a Redis-backed registry, not a
 *        per-worker dict), so this no longer depends on which worker a
 *        socket happened to land on.
 *   1011 `internal` (:559) - an exception in the forward loop. uvicorn's own
 *        keepalive timeout also closes with 1011, so the copy says only that
 *        the server ended it, which covers both.
 *   4400 `bad_message` (:431) - the first frame was not a valid auth message.
 *   4403 `forbidden` (:477) - the caller is not in the scan's team.
 *   4404 `scan_not_found` (:408 and :472) - the id in the URL is not a UUID,
 *        OR the row is absent. "There is no scan with this id" is the one
 *        sentence true of both.
 *   4429 `capacity_at_limit` (W4) - the global connection cap is full. Unlike
 *        1001, this has nothing to do with this account's own tabs.
 *
 * Everything else falls back to the network sentence, which is right because
 * the fallback case is 1006: the browser's own code for "no close frame
 * arrived at all". The server never sends it. 1008 is absent on purpose, that
 * path signs the reader out, so this panel never renders for it.
 */
type StreamStoppedReason =
  | "network"
  | "evicted"
  | "server"
  | "rejected"
  | "forbidden"
  | "missing"
  | "at_capacity";

const REASON_BY_CLOSE_CODE: Record<number, StreamStoppedReason> = {
  1001: "evicted",
  1011: "server",
  4400: "rejected",
  4403: "forbidden",
  4404: "missing",
  4429: "at_capacity",
};

/** Codes where pressing Reconnect would only repeat the same refusal. */
const UNRETRYABLE_CLOSE_CODES = new Set([4400, 4403, 4404]);
// 4429 (at_capacity) is deliberately NOT in this set, capacity is
// transient (it frees up as other connections close), unlike the codes
// above, which are permanent for this URL/session. The reader keeps the
// Reconnect button; the hook just will not retry it automatically.

/**
 * Reasons after which "the scan itself is unaffected" is still true.
 *
 * It is the sentence a reader most wants when a stream dies, and for a
 * dropped connection or an evicted socket it is exactly right: the scan runs
 * in a worker that never knew this socket existed. The same is true of a
 * capacity refusal: it happens before the socket is admitted at all, so it
 * cannot have touched the scan.
 *
 * Three reasons do not get it. For 4403 and 4404 it is nonsense - "there is
 * no scan with this id, the scan itself is unaffected and is still running"
 * was on screen until this was made conditional. For 1011 it is a guess: the
 * gateway subscribes to the same Redis instance Celery uses as its broker, so
 * one of the two things that produce 1011 is a Redis failure, and a Redis
 * failure stops the scan as surely as it stops the stream. The reader sees
 * this panel only after five minutes of that, by which time "still running"
 * is more likely false than true.
 */
const SCAN_UNAFFECTED_REASONS = new Set<StreamStoppedReason>([
  "network",
  "evicted",
  "rejected",
  "at_capacity",
]);

/**
 * Reasons where the honest thing left to say is about this screen, not the
 * scan.
 *
 * Taking the reassurance off 1011 was right, but it left the panel saying
 * nothing at all about the thing the reader is asking, and a screen that goes
 * quiet is the failure this whole unit exists to remove. What is true either
 * way: the surface froze at the last frame that arrived. It claims nothing
 * about whether the scan lived, and it explains what the Reconnect button is
 * for.
 */
const STALE_REASONS = new Set<StreamStoppedReason>(["server"]);

/** Which surface froze. Decides the heading, the stale sentence, and testids. */
export type StreamSurface = "progress" | "log";

const SURFACE_COPY: Record<
  StreamSurface,
  { titleKey: string; staleKey: string; testId: string }
> = {
  progress: {
    titleKey: "stream_stopped.title",
    staleKey: "stream_stopped.stale",
    testId: "scan-progress",
  },
  log: {
    titleKey: "stream_stopped.title_log",
    staleKey: "stream_stopped.stale_log",
    testId: "scan-detail-page-log",
  },
};

export function StreamStopped({
  closeCode,
  onReconnect,
  surface = "progress",
}: {
  closeCode: number | null;
  onReconnect: () => void;
  surface?: StreamSurface;
}) {
  const { t } = useTranslation("scans");
  const copy = SURFACE_COPY[surface];
  const reasonKey = REASON_BY_CLOSE_CODE[closeCode ?? -1] ?? "network";
  // Reconnecting cannot help when the answer will be the same: the scan is
  // not there, the reader cannot see it, or the browser sent something the
  // server would reject again. Offering the button anyway would be offering
  // to repeat a refusal.
  const retryable = !UNRETRYABLE_CLOSE_CODES.has(closeCode ?? -1);

  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-border bg-muted/40 p-3"
      data-testid={`${copy.testId}-stopped`}
      data-close-code={closeCode ?? ""}
      role="status"
    >
      <div className="flex items-start gap-2">
        <PlugZap className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0">
          <p className="text-sm font-medium">{t(copy.titleKey)}</p>
          <p className="text-xs text-muted-foreground">
            {t(`stream_stopped.reason.${reasonKey}`)}
            {SCAN_UNAFFECTED_REASONS.has(reasonKey)
              ? ` ${t("stream_stopped.unaffected")}`
              : null}
            {STALE_REASONS.has(reasonKey) ? ` ${t(copy.staleKey)}` : null}
          </p>
        </div>
      </div>
      {retryable ? (
        <div>
          <Button
            variant="outline"
            size="sm"
            onClick={onReconnect}
            data-testid={`${copy.testId}-reconnect`}
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            {t("stream_stopped.retry")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
