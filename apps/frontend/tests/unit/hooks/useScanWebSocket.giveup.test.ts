/**
 * When the scan stream stops trying (C4).
 *
 * The five-minute reconnect budget and the state it lands in had no coverage
 * at all: the longest span any existing test simulated was sixty seconds, so
 * nothing exercised the branch that gives up, and nothing noticed that the
 * panel went on saying "reconnecting" afterwards. These are the cases that
 * separate "still trying" from "stopped", which is the whole point of the
 * flag the panel now reads.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useScanWebSocket } from "@/hooks/useScanWebSocket";
import { useAuthStore } from "@/stores/authStore";

class FakeSocket {
  static instances: FakeSocket[] = [];
  url: string;
  readyState = 0;
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
    this.onclose?.({
      code: code ?? 1000,
      reason: reason ?? "",
      wasClean: true,
    } as CloseEvent);
  }

  __open() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  __closeFromServer(code: number, reason = "") {
    this.readyState = 3;
    this.onclose?.({ code, reason, wasClean: false } as CloseEvent);
  }
}

const factory = (url: string) => new FakeSocket(url) as unknown as WebSocket;

/** The backoff ladder, so a test can walk it without hard-coding a sum. */
const BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 30_000];

function backoffFor(attemptIndex: number) {
  return BACKOFF_MS[Math.min(attemptIndex, BACKOFF_MS.length - 1)];
}

/**
 * Drives failed reconnects until the hook gives up, or until `maxRounds`.
 *
 * Walks the real ladder rather than jumping the clock in one go: the budget
 * is checked when a reconnect is *scheduled*, so a single five-minute jump
 * would skip every scheduling call and the give-up would never fire.
 */
async function failUntilGiveUp(maxRounds = 40) {
  for (let round = 0; round < maxRounds; round += 1) {
    const socket = FakeSocket.instances[FakeSocket.instances.length - 1];
    act(() => socket.__closeFromServer(1006));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(backoffFor(round) + 1);
    });
    if (FakeSocket.instances.length === round + 1) return round + 1;
  }
  return null;
}

describe("useScanWebSocket give-up", () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.useFakeTimers();
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

  it("keeps trying while the budget lasts", async () => {
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());

    act(() => FakeSocket.instances[0].__closeFromServer(1006));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_001);
    });

    expect(FakeSocket.instances).toHaveLength(2);
    expect(result.current.gaveUp).toBe(false);
    expect(result.current.reconnectAttempt).toBe(1);
  });

  it("gives up once the budget is spent, and says so distinctly", async () => {
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());

    const rounds = await failUntilGiveUp();

    expect(rounds, "the hook never stopped trying").not.toBeNull();
    expect(result.current.gaveUp).toBe(true);
    // Roughly five minutes of ladder: four short slots then 30 s apiece.
    // Asserted as a range because the exact count is arithmetic, not
    // contract, and pinning it exactly would break on a tuning change.
    expect(rounds).toBeGreaterThan(5);
    expect(rounds).toBeLessThan(20);
  });

  it("does not quietly start again when the tab comes back", async () => {
    // It used to. The budget clock was never cleared either, so the panel
    // left its stopped state and returned to it on the next failure.
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());
    await failUntilGiveUp();
    expect(result.current.gaveUp).toBe(true);
    const socketsWhenStopped = FakeSocket.instances.length;

    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(FakeSocket.instances).toHaveLength(socketsWhenStopped);
    expect(result.current.gaveUp).toBe(true);
  });

  it("starts again when asked, from the top of the ladder", async () => {
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());
    await failUntilGiveUp();
    expect(result.current.gaveUp).toBe(true);
    const socketsWhenStopped = FakeSocket.instances.length;

    act(() => result.current.reconnect());

    expect(FakeSocket.instances).toHaveLength(socketsWhenStopped + 1);
    expect(result.current.gaveUp).toBe(false);
    // The counter resets too. Coming back on "Attempt 14" would tell the
    // reader their fresh request was the fourteenth try.
    expect(result.current.reconnectAttempt).toBe(0);

    // And the budget went with it: one failure after a manual reconnect
    // schedules another go rather than giving up on the spot.
    act(() =>
      FakeSocket.instances[FakeSocket.instances.length - 1].__closeFromServer(
        1006,
      ),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_001);
    });
    expect(FakeSocket.instances).toHaveLength(socketsWhenStopped + 2);
    expect(result.current.gaveUp).toBe(false);
  });

  it("gives up immediately on a close it will not retry", async () => {
    // An eviction by a newer connection. An open scan page holds two of the
    // three connections this account may have on one worker, so a second tab
    // can take the first tab's stream away. That tab used to sit there
    // claiming to reconnect while nothing did.
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());

    act(() => FakeSocket.instances[0].__closeFromServer(1001, "newer_connection"));

    expect(result.current.gaveUp).toBe(true);
    expect(result.current.closeCode).toBe(1001);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("does not call an expired session a give-up", async () => {
    // 1008 signs the reader out. A Reconnect button there would offer to
    // retry something that is about to navigate away.
    const onAuthExpired = vi.fn();
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory, onAuthExpired }),
    );
    act(() => FakeSocket.instances[0].__open());

    act(() => FakeSocket.instances[0].__closeFromServer(1008, "auth_invalid"));

    expect(onAuthExpired).toHaveBeenCalledOnce();
    expect(result.current.gaveUp).toBe(false);
  });

  it("does not call a close we sent ourselves a give-up", async () => {
    // The hook closes with 1000 in three places of its own, and 1000 is in
    // the no-reconnect set, so without the guard the panel would tell a
    // reader the stream stopped when the hook was the one that stopped it.
    //
    // Driven through the token-loss path rather than unmount: cleanup sets
    // the cancelled flag before closing, so an unmount never reaches the
    // guard at all and a test using it asserts nothing.
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );

    // The token goes while the handshake is in flight, so the hook closes
    // its own socket with 1000 on open. That is the only path the guard is
    // on: unmount sets the cancelled flag before closing, so a test driven
    // through unmount never reaches it and asserts nothing.
    act(() => {
      useAuthStore.setState({ accessToken: null });
    });
    act(() => FakeSocket.instances[0].__open());

    expect(FakeSocket.instances[0].sent).toHaveLength(0);
    expect(result.current.closeCode).toBe(1000);
    expect(result.current.gaveUp).toBe(false);
  });

  it("is a no-op while a socket is already alive", async () => {
    // The documented contract, and a leak if it is not honoured: a second
    // socket would be opened without closing the first, the ref would be
    // overwritten, and the account would hold one more connection than it
    // thinks - which is what evicts a reader's other tab.
    const { result } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());
    expect(FakeSocket.instances).toHaveLength(1);

    act(() => result.current.reconnect());
    act(() => result.current.reconnect());

    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("does not call our own unmount close a give-up", async () => {
    const { result, unmount } = renderHook(() =>
      useScanWebSocket("scan-1", { socketFactory: factory }),
    );
    act(() => FakeSocket.instances[0].__open());

    unmount();

    expect(result.current.gaveUp).toBe(false);
  });
});
