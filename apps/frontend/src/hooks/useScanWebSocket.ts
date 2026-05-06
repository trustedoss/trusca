/**
 * useScanWebSocket — Phase 2 PR #9 task 2.10.
 *
 * Connects to `ws(s)://<host>/ws/scans/<scan_id>` and streams progress
 * frames per the backend contract pinned in apps/backend/api/v1/ws.py.
 *
 * Lifecycle:
 *   1. Mount → state="connecting" → open WebSocket.
 *   2. onopen → state="authenticating" → send `{type:"auth", token}` (read
 *      from the auth store at send time, never cached).
 *   3. Server emits initial sync frame → state="open" → onmessage drives
 *      `lastMessage` updates.
 *   4. Terminal step (succeeded / failed) → close 1000 ourselves and stay
 *      in state="closed" with `isTerminal=true`. No reconnect.
 *
 * Reconnect policy (close codes — see ws.py):
 *   - 1000 (normal) / 1001 (newer_connection) → no reconnect.
 *   - 1008 (auth_*)                            → dispatch `auth:expired`,
 *                                                stop reconnecting.
 *   - 4400 (bad_message) / 4403 / 4404         → no reconnect (client bug
 *                                                or server-rejected access).
 *   - 1011 / network failure                   → exponential backoff
 *                                                (1s → 2s → 4s → 8s → 30s).
 *                                                Stop after 5 minutes
 *                                                cumulative.
 *
 * StrictMode + HMR safety: every effect cleans up its socket and timers, so
 * the double-mount pattern never leaks. Tests stub `globalThis.WebSocket`.
 */
import { useEffect, useRef, useState } from "react";

import { buildScanProgressUrl } from "@/lib/wsBase";
import { useAuthStore } from "@/stores/authStore";

export type ScanStep =
  | "bootstrap"
  | "fetch"
  | "cdxgen"
  | "ort"
  | "dt_upload"
  | "dt_findings"
  | "finalize"
  | "succeeded"
  | "failed"
  // The publisher may emit other strings if the pipeline grows; keeping
  // the type open at the boundary keeps callers honest.
  | (string & {});

export interface ScanProgressMessage {
  percent: number;
  step: ScanStep;
  ts: string;
}

export type ScanWebSocketState =
  | "idle"
  | "connecting"
  | "authenticating"
  | "open"
  | "closed"
  | "error";

export interface UseScanWebSocketResult {
  state: ScanWebSocketState;
  lastMessage: ScanProgressMessage | null;
  closeCode: number | null;
  closeReason: string | null;
  reconnectAttempt: number;
  /** True once the latest frame's step is a terminal value (succeeded/failed). */
  isTerminal: boolean;
}

interface UseScanWebSocketOptions {
  /** Disable the connection without unmounting the component. */
  enabled?: boolean;
  /**
   * Override the WebSocket constructor (tests inject a mock). Real callers
   * should leave this unset — we read globalThis.WebSocket at connect time.
   */
  socketFactory?: (url: string) => WebSocket;
  /**
   * Override the URL builder. Tests use this so they don't need to mock
   * `import.meta.env`.
   */
  urlBuilder?: (scanId: string) => string;
  /**
   * Override the dispatcher for `auth:expired`. Tests use this to assert.
   */
  onAuthExpired?: () => void;
}

// Reconnect schedule (ms). The 5th and beyond cap at 30s.
const RECONNECT_BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 30_000];
// Stop reconnecting once cumulative attempts have spanned 5 minutes —
// the user is almost certainly offline.
const RECONNECT_BUDGET_MS = 5 * 60_000;

const TERMINAL_STEPS: ReadonlySet<string> = new Set(["succeeded", "failed"]);

const NO_RECONNECT_CODES: ReadonlySet<number> = new Set([
  1000, 1001, 1008, 4400, 4403, 4404,
]);

function isTerminalStep(step: string | null | undefined): boolean {
  return typeof step === "string" && TERMINAL_STEPS.has(step);
}

function pickBackoff(attempt: number): number {
  const idx = Math.min(attempt, RECONNECT_BACKOFF_MS.length - 1);
  return RECONNECT_BACKOFF_MS[idx];
}

export function useScanWebSocket(
  scanId: string | null,
  opts: UseScanWebSocketOptions = {},
): UseScanWebSocketResult {
  const { enabled = true, socketFactory, urlBuilder, onAuthExpired } = opts;

  const [state, setState] = useState<ScanWebSocketState>("idle");
  const [lastMessage, setLastMessage] = useState<ScanProgressMessage | null>(
    null,
  );
  const [closeCode, setCloseCode] = useState<number | null>(null);
  const [closeReason, setCloseReason] = useState<string | null>(null);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);

  // Refs survive across renders and StrictMode double-invocation. We use
  // refs (not state) for anything the cleanup path needs to read without
  // triggering a re-render.
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectStartedAtRef = useRef<number | null>(null);
  const cancelledRef = useRef(false);
  const terminalReachedRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    terminalReachedRef.current = false;

    if (!enabled || scanId == null || scanId === "") {
      // Nothing to do — make sure we report idle and clear any leftover state.
      setState("idle");
      return () => {
        // No-op — nothing was opened.
      };
    }

    function clearReconnectTimer() {
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    }

    function dispatchAuthExpired() {
      if (onAuthExpired) {
        onAuthExpired();
        return;
      }
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("auth:expired"));
      }
    }

    function open(attempt: number) {
      if (cancelledRef.current) {
        return;
      }
      const builder = urlBuilder ?? buildScanProgressUrl;
      const url = builder(scanId as string);

      // Read the token *now*, never at module scope (CLAUDE.md §11).
      const token = useAuthStore.getState().accessToken;
      if (!token) {
        // No token yet — flip to closed and wait for a remount once auth
        // bootstraps. RequireAuth gates on the same store so production
        // routes never reach here token-less, but tests can.
        setState("closed");
        return;
      }

      setState("connecting");

      let socket: WebSocket;
      try {
        const factory =
          socketFactory ??
          ((u: string) => new (globalThis.WebSocket as typeof WebSocket)(u));
        socket = factory(url);
      } catch (err) {
        // WebSocket constructor can throw on hostile URLs — treat like a
        // network failure and back off.
        console.error("useScanWebSocket: socket constructor failed", err);
        setState("error");
        scheduleReconnect(attempt);
        return;
      }

      socketRef.current = socket;

      socket.onopen = () => {
        if (cancelledRef.current) {
          return;
        }
        setState("authenticating");
        // Re-read the token at send time so a refresh that landed between
        // construction and open uses the freshest value.
        const sendToken = useAuthStore.getState().accessToken;
        if (!sendToken) {
          // Lost the token while the handshake was in-flight; close
          // gracefully and let auth bootstrap re-trigger.
          try {
            socket.close(1000, "no_token");
          } catch {
            // Ignore — already-closed sockets throw on .close in some
            // environments.
          }
          return;
        }
        try {
          socket.send(JSON.stringify({ type: "auth", token: sendToken }));
        } catch (err) {
          console.error("useScanWebSocket: send(auth) failed", err);
        }
      };

      socket.onmessage = (event: MessageEvent) => {
        if (cancelledRef.current) {
          return;
        }
        let parsed: ScanProgressMessage | null = null;
        try {
          const raw =
            typeof event.data === "string"
              ? event.data
              : new TextDecoder().decode(event.data as ArrayBuffer);
          const obj = JSON.parse(raw) as Partial<ScanProgressMessage>;
          if (
            typeof obj?.percent === "number" &&
            typeof obj?.step === "string" &&
            typeof obj?.ts === "string"
          ) {
            parsed = {
              percent: obj.percent,
              step: obj.step,
              ts: obj.ts,
            };
          }
        } catch (err) {
          console.error("useScanWebSocket: bad frame", err);
        }
        if (parsed != null) {
          setLastMessage(parsed);
          setState("open");
          // Reset the reconnect budget once we've received a valid frame.
          reconnectStartedAtRef.current = null;
          setReconnectAttempt(0);
          if (isTerminalStep(parsed.step)) {
            terminalReachedRef.current = true;
            // Close cleanly; the close handler will record code 1000 and
            // skip reconnect.
            try {
              socket.close(1000, "terminal");
            } catch {
              // Already-closed is fine.
            }
          }
        }
      };

      socket.onerror = () => {
        // The Browser fires a generic Event without code/reason. The close
        // handler runs immediately after with the real disposition — let it
        // own the reconnect decision.
        if (cancelledRef.current) {
          return;
        }
        setState("error");
      };

      socket.onclose = (event: CloseEvent) => {
        if (cancelledRef.current) {
          return;
        }
        setCloseCode(event.code);
        setCloseReason(event.reason || null);
        socketRef.current = null;

        // Terminal step → we initiated the close ourselves; stay closed.
        if (terminalReachedRef.current) {
          setState("closed");
          return;
        }

        // 1008 → auth: bounce to login, no reconnect.
        if (event.code === 1008) {
          setState("closed");
          dispatchAuthExpired();
          return;
        }

        if (NO_RECONNECT_CODES.has(event.code)) {
          setState("closed");
          if (event.code === 4400) {
            console.error(
              "useScanWebSocket: server rejected first frame (4400)",
              event.reason,
            );
          }
          return;
        }

        // Everything else (1011, 1006 transport, 0) → reconnect.
        setState("closed");
        scheduleReconnect(attempt);
      };
    }

    function scheduleReconnect(prevAttempt: number) {
      if (cancelledRef.current) {
        return;
      }
      if (reconnectStartedAtRef.current == null) {
        reconnectStartedAtRef.current = Date.now();
      }
      const elapsed = Date.now() - reconnectStartedAtRef.current;
      if (elapsed > RECONNECT_BUDGET_MS) {
        // Give up — the user has been offline long enough that something
        // bigger has gone wrong. Surfacing this as state="error" lets the
        // UI show the "Reconnect" button.
        setState("error");
        return;
      }
      const nextAttempt = prevAttempt + 1;
      const delay = pickBackoff(prevAttempt);
      setReconnectAttempt(nextAttempt);
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        open(nextAttempt);
      }, delay);
    }

    open(0);

    return () => {
      cancelledRef.current = true;
      clearReconnectTimer();
      const sock = socketRef.current;
      socketRef.current = null;
      if (sock != null) {
        try {
          // 1000 = normal closure. We deliberately close even mid-handshake
          // so HMR / StrictMode double-mounts do not leak.
          sock.close(1000, "unmount");
        } catch {
          // Already closed.
        }
      }
    };
    // We deliberately re-run when scanId / enabled change. Other deps are
    // refs / module-level — they don't trigger this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanId, enabled]);

  return {
    state,
    lastMessage,
    closeCode,
    closeReason,
    reconnectAttempt,
    isTerminal: isTerminalStep(lastMessage?.step ?? null),
  };
}
