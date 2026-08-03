/**
 * useScanWebSocket — tab-visibility reconnect (Phase 6 PR #19 chore D).
 *
 * The hook listens on `document.visibilitychange`. When the tab regains
 * focus while a backoff timer is pending OR the socket is in a
 * closing/closed state, the pending timer is cancelled and the hook
 * re-attempts the connection immediately — without resetting the
 * 5-minute cumulative budget.
 *
 * We reuse the FakeSocket pattern from `useScanWebSocket.test.ts` but
 * pin `vi.useFakeTimers({ shouldAdvanceTime: true })` so React still
 * flushes microtasks in between visibility transitions.
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

  __open() {
    this.readyState = 1;
    if (this.onopen) this.onopen(new Event("open"));
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

const factory = (url: string) => new FakeSocket(url) as unknown as WebSocket;

/**
 * Drive the document into the requested visibility state and dispatch a
 * `visibilitychange` event. jsdom does not own a visibility model so we
 * patch `document.visibilityState` via Object.defineProperty.
 */
function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("useScanWebSocket visibility reconnect", () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    useAuthStore.setState({
      user: null,
      accessToken: "tok-test",
      status: "authenticated",
      isAuthenticated: true,
    });
    setVisibility("visible");
  });
  afterEach(() => {
    vi.useRealTimers();
    useAuthStore.getState().reset();
    setVisibility("visible");
  });

  it("cancels a pending backoff timer and reconnects immediately when the tab becomes visible", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderHook(() => useScanWebSocket("scan-1", { socketFactory: factory }));
    expect(FakeSocket.instances).toHaveLength(1);

    // Simulate a transport failure → triggers exponential backoff
    // (first slot: 1s).
    act(() => FakeSocket.instances[0].__open());
    act(() => FakeSocket.instances[0].__closeFromServer(1011, "internal"));

    // Backoff is queued; another socket has not been constructed yet.
    expect(FakeSocket.instances).toHaveLength(1);

    // Hide and re-show the tab BEFORE the 1s slot elapses. The handler
    // must cancel the pending timer and call open() now.
    act(() => setVisibility("hidden"));
    await act(async () => {
      // Tiny tick; not enough to fire the 1s backoff naturally.
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(FakeSocket.instances).toHaveLength(1);

    act(() => setVisibility("visible"));

    // Immediate reconnect — no waiting on the 1s slot.
    await waitFor(() => {
      expect(FakeSocket.instances).toHaveLength(2);
    });

    // Sanity check: the original 1s timer no longer fires another socket
    // open. Advance past the backoff slot — still 2 instances.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it("does nothing when the tab becomes visible while the socket is healthy", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderHook(() => useScanWebSocket("scan-1", { socketFactory: factory }));
    act(() => FakeSocket.instances[0].__open());

    // Healthy connection — visibilitychange should be a no-op.
    act(() => setVisibility("hidden"));
    act(() => setVisibility("visible"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("does nothing on visibilitychange when the tab is hidden", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderHook(() => useScanWebSocket("scan-1", { socketFactory: factory }));
    expect(FakeSocket.instances).toHaveLength(1);
    act(() => FakeSocket.instances[0].__open());
    act(() => FakeSocket.instances[0].__closeFromServer(1011, "internal"));
    expect(FakeSocket.instances).toHaveLength(1);

    // Hidden → must NOT trigger reconnect.
    act(() => setVisibility("hidden"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("removes the visibilitychange listener on unmount", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const removeSpy = vi.spyOn(document, "removeEventListener");
    const { unmount } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    unmount();
    const calls = removeSpy.mock.calls.filter(
      ([event]) => event === "visibilitychange",
    );
    expect(calls.length).toBeGreaterThan(0);
    removeSpy.mockRestore();
  });
});
