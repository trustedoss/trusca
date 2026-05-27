/**
 * useScanWebSocket — unit tests (PR #9 task 2.10).
 *
 * We swap a stub WebSocket implementation through the hook's `socketFactory`
 * option so the tests never touch a real network. The fake socket exposes
 * triggers (`__open`, `__message`, `__closeFromServer`) so we can drive
 * every branch of the lifecycle deterministically.
 *
 * Where reconnect-backoff timing matters we use vitest's fake timers but
 * with `shouldAdvanceTime: true` so React's microtasks still flush.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useScanWebSocket } from "@/hooks/useScanWebSocket";
import { useAuthStore } from "@/stores/authStore";

class FakeSocket {
  static instances: FakeSocket[] = [];
  url: string;
  readyState: number = 0; // CONNECTING
  sent: string[] = [];
  onopen: ((ev?: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close(code?: number, reason?: string) {
    this.readyState = 3;
    if (this.onclose) {
      this.onclose({
        code: code ?? 1000,
        reason: reason ?? "",
        wasClean: true,
      } as CloseEvent);
    }
  }

  // Test helpers
  __open() {
    this.readyState = 1;
    if (this.onopen) this.onopen(new Event("open"));
  }

  __message(payload: unknown) {
    if (this.onmessage)
      this.onmessage(new MessageEvent("message", { data: JSON.stringify(payload) }));
  }

  __closeFromServer(code: number, reason: string = "") {
    this.readyState = 3;
    if (this.onclose) {
      this.onclose({
        code,
        reason,
        wasClean: false,
      } as CloseEvent);
    }
  }
}

const factory = (url: string) =>
  new FakeSocket(url) as unknown as WebSocket;

describe("useScanWebSocket", () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    useAuthStore.setState({
      user: null,
      accessToken: "tok-test",
      status: "authenticated",
      isAuthenticated: true,
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    useAuthStore.getState().reset();
  });

  it("does not connect when scanId is null", () => {
    const { result } = renderHook(() =>
      useScanWebSocket(null, { socketFactory: factory }),
    );
    expect(FakeSocket.instances).toHaveLength(0);
    expect(result.current.state).toBe("idle");
  });

  it("does not connect when enabled=false", () => {
    renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory, enabled: false }),
    );
    expect(FakeSocket.instances).toHaveLength(0);
  });

  it("opens and sends the auth frame with the current access token", async () => {
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    expect(FakeSocket.instances).toHaveLength(1);
    expect(result.current.state).toBe("connecting");
    act(() => FakeSocket.instances[0].__open());
    await waitFor(() => {
      expect(FakeSocket.instances[0].sent).toHaveLength(1);
    });
    const payload = JSON.parse(FakeSocket.instances[0].sent[0]);
    expect(payload).toEqual({ type: "auth", token: "tok-test" });
    expect(result.current.state).toBe("authenticating");
  });

  it("parses progress frames and updates lastMessage + state", async () => {
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 42,
        step: "cdxgen",
        ts: "2026-05-06T12:00:00.000Z",
      }),
    );
    await waitFor(() => {
      expect(result.current.lastMessage?.percent).toBe(42);
    });
    expect(result.current.state).toBe("open");
    expect(result.current.lastMessage?.step).toBe("cdxgen");
    expect(result.current.isTerminal).toBe(false);
  });

  it("marks isTerminal=true on succeeded and closes the socket", async () => {
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 100,
        step: "succeeded",
        ts: "2026-05-06T12:01:00.000Z",
      }),
    );
    await waitFor(() => {
      expect(result.current.isTerminal).toBe(true);
    });
    // The hook initiates the close itself with code 1000.
    expect(FakeSocket.instances[0].readyState).toBe(3);
  });

  it("marks isTerminal=true on a cancelled frame and closes the socket (BUG-007)", async () => {
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 90,
        step: "cancelled",
        ts: "2026-05-24T12:01:00.000Z",
      }),
    );
    await waitFor(() => {
      expect(result.current.isTerminal).toBe(true);
    });
    // The hook initiates a clean close itself (terminal disposition).
    expect(FakeSocket.instances[0].readyState).toBe(3);
  });

  it("fires onNonTerminalClose when the socket closes without a terminal frame (BUG-007)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onNonTerminalClose = vi.fn();
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory, onNonTerminalClose }),
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 90,
        step: "dt_findings",
        ts: "2026-05-24T12:00:00.000Z",
      }),
    );
    // Server-side cancel revokes the worker; the stream drops with 1011 and no
    // `cancelled` frame is ever published.
    act(() => FakeSocket.instances[0].__closeFromServer(1011, "internal"));
    await waitFor(() => {
      expect(onNonTerminalClose).toHaveBeenCalledWith(1011);
    });
    expect(result.current.isTerminal).toBe(false);
  });

  it("does NOT fire onNonTerminalClose on a terminal frame close (BUG-007)", async () => {
    const onNonTerminalClose = vi.fn();
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory, onNonTerminalClose }),
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 100,
        step: "succeeded",
        ts: "2026-05-24T12:02:00.000Z",
      }),
    );
    await waitFor(() => {
      expect(result.current.isTerminal).toBe(true);
    });
    expect(onNonTerminalClose).not.toHaveBeenCalled();
  });

  it("does NOT fire onNonTerminalClose on an auth bounce (1008) (BUG-007)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onNonTerminalClose = vi.fn();
    const onAuthExpired = vi.fn();
    renderHook(() =>
      useScanWebSocket("scan-1", {
        socketFactory: factory,
        onNonTerminalClose,
        onAuthExpired,
      }),
    );
    act(() => FakeSocket.instances[0].__open());
    act(() => FakeSocket.instances[0].__closeFromServer(1008, "auth_invalid"));
    await waitFor(() => {
      expect(onAuthExpired).toHaveBeenCalledTimes(1);
    });
    // Auth bounce → refetch would 401, so the fallback must stay quiet.
    expect(onNonTerminalClose).not.toHaveBeenCalled();
  });

  it("dispatches auth:expired and stops reconnecting on close 1008", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onAuthExpired = vi.fn();
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", {
        socketFactory: factory,
        onAuthExpired,
      }),
    );
    act(() => FakeSocket.instances[0].__open());
    act(() => FakeSocket.instances[0].__closeFromServer(1008, "auth_invalid"));
    await waitFor(() => {
      expect(onAuthExpired).toHaveBeenCalledTimes(1);
    });
    // Advance well beyond the longest backoff to ensure no reconnect.
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(FakeSocket.instances).toHaveLength(1);
    expect(result.current.closeCode).toBe(1008);
  });

  it("schedules reconnect with exponential backoff on close 1011", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());
    act(() => FakeSocket.instances[0].__closeFromServer(1011, "internal"));
    await waitFor(() => {
      expect(result.current.closeCode).toBe(1011);
    });
    expect(result.current.reconnectAttempt).toBe(1);
    // Advance the first 1s backoff
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it("does not reconnect on 4404 (scan not found)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderHook(() => useScanWebSocket("scan-1", { socketFactory: factory }));
    act(() => FakeSocket.instances[0].__open());
    act(() => FakeSocket.instances[0].__closeFromServer(4404, "not_found"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("cleans up the socket on unmount", () => {
    const { unmount } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    expect(FakeSocket.instances).toHaveLength(1);
    expect(FakeSocket.instances[0].readyState).toBe(0);
    unmount();
    expect(FakeSocket.instances[0].readyState).toBe(3);
  });

  it("ignores malformed frames", () => {
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());
    // Frame missing 'percent' — should be silently dropped.
    act(() => {
      if (FakeSocket.instances[0].onmessage) {
        FakeSocket.instances[0].onmessage(
          new MessageEvent("message", { data: "{not json" }),
        );
      }
    });
    expect(result.current.lastMessage).toBeNull();
  });

  it("does not connect when token is null", () => {
    useAuthStore.setState({
      user: null,
      accessToken: null,
      status: "anonymous",
      isAuthenticated: false,
    });
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    // Hook constructed the socket then realised no token; state goes to closed.
    expect(result.current.state).toBe("closed");
  });

  // ---------------------------------------------------------------------
  // P2 #8c — tool log frames (cdxgen / scancode stdout / stderr streaming)
  // ---------------------------------------------------------------------

  it("parses log frames into logMessages without touching lastMessage", async () => {
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        type: "log",
        stage: "cdxgen",
        stream: "stdout",
        line: "resolving package tree…",
        ts: "2026-05-26T12:00:00.000Z",
      }),
    );
    await waitFor(() => {
      expect(result.current.logMessages).toHaveLength(1);
    });
    expect(result.current.logMessages[0]).toEqual({
      type: "log",
      stage: "cdxgen",
      stream: "stdout",
      line: "resolving package tree…",
      ts: "2026-05-26T12:00:00.000Z",
    });
    // Log frames must NOT clobber progress state.
    expect(result.current.lastMessage).toBeNull();
    expect(result.current.messages).toHaveLength(0);
    // Receiving a valid frame transitions the hook into "open".
    expect(result.current.state).toBe("open");
  });

  it("appends stderr log frames in arrival order", async () => {
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        type: "log",
        stage: "scancode",
        stream: "stdout",
        line: "starting",
        ts: "2026-05-26T12:00:00.000Z",
      }),
    );
    act(() =>
      FakeSocket.instances[0].__message({
        type: "log",
        stage: "scancode",
        stream: "stderr",
        line: "warning: licenseref unknown",
        ts: "2026-05-26T12:00:01.000Z",
      }),
    );
    await waitFor(() => {
      expect(result.current.logMessages).toHaveLength(2);
    });
    expect(result.current.logMessages[0].stream).toBe("stdout");
    expect(result.current.logMessages[1].stream).toBe("stderr");
    expect(result.current.logMessages[1].line).toBe(
      "warning: licenseref unknown",
    );
  });

  it("treats a progress frame without explicit type as progress (back-compat)", async () => {
    // Older backends did not emit `type: "progress"` — the hook must keep
    // accepting bare {percent, step, ts} envelopes.
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 18,
        step: "prep",
        ts: "2026-05-26T12:00:00.000Z",
      }),
    );
    await waitFor(() => {
      expect(result.current.lastMessage?.percent).toBe(18);
    });
    expect(result.current.lastMessage?.step).toBe("prep");
    expect(result.current.logMessages).toHaveLength(0);
  });

  it("rejects log frames with an unknown stream value", async () => {
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        type: "log",
        stage: "cdxgen",
        stream: "weird",
        line: "should be dropped",
        ts: "2026-05-26T12:00:00.000Z",
      }),
    );
    // No assertion on logMessages — it should stay empty. We give the
    // microtask queue a tick first.
    await Promise.resolve();
    expect(result.current.logMessages).toHaveLength(0);
  });

  it("clears logMessages when reconnecting to a fresh scanId", async () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: string }) =>
        useScanWebSocket(id, { socketFactory: factory }),
      { initialProps: { id: "scan-1" } },
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        type: "log",
        stage: "cdxgen",
        stream: "stdout",
        line: "first scan line",
        ts: "2026-05-26T12:00:00.000Z",
      }),
    );
    await waitFor(() => {
      expect(result.current.logMessages).toHaveLength(1);
    });

    // Switch to a new scan id — buffer must reset, not carry the previous
    // scan's lines into the new panel.
    rerender({ id: "scan-2" });
    expect(result.current.logMessages).toHaveLength(0);
  });
});
