/**
 * ScanProgress — unit tests (PR #9 task 2.10).
 *
 * The component reads from `useScanWebSocket`, which we drive via the
 * `socketFactory` injection seam. No real WebSocket is created.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ScanProgress } from "@/features/scan/ScanProgress";
import { useAuthStore } from "@/stores/authStore";

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState: number = 0;
  onopen: ((ev?: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  url: string;

  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }

  send(_data: string) {
    // no-op
  }

  close(code?: number, reason?: string) {
    this.readyState = 3;
    if (this.onclose) {
      this.onclose({ code: code ?? 1000, reason: reason ?? "", wasClean: true } as CloseEvent);
    }
  }

  __open() {
    this.readyState = 1;
    if (this.onopen) this.onopen(new Event("open"));
  }

  __message(payload: unknown) {
    if (this.onmessage)
      this.onmessage(new MessageEvent("message", { data: JSON.stringify(payload) }));
  }
}

const factory = (url: string) => new FakeSocket(url) as unknown as WebSocket;

describe("ScanProgress", () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    useAuthStore.setState({
      user: null,
      accessToken: "tok-progress",
      status: "authenticated",
      isAuthenticated: true,
    });
  });
  afterEach(() => {
    useAuthStore.getState().reset();
    vi.useRealTimers();
  });

  it("renders the title and the 7-step pipeline list", async () => {
    render(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    expect(screen.getByTestId("scan-progress")).toBeInTheDocument();
    const steps = screen.getByTestId("scan-progress-steps");
    expect(steps.querySelectorAll("[data-step]")).toHaveLength(7);
  });

  it("shows skeleton during connecting state", () => {
    render(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    expect(
      screen.getByTestId("scan-progress-skeleton"),
    ).toBeInTheDocument();
  });

  it("renders progress and marks the current step on incoming frame", async () => {
    render(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 35,
        step: "cdxgen",
        ts: "2026-05-06T12:00:00.000Z",
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("scan-progress-percent")).toHaveTextContent("35%");
    });
    const cdxgenItem = screen
      .getByTestId("scan-progress-steps")
      .querySelector('[data-step="cdxgen"]');
    expect(cdxgenItem).toHaveAttribute("data-state", "current");
    // Earlier steps are completed.
    const fetchItem = screen
      .getByTestId("scan-progress-steps")
      .querySelector('[data-step="fetch"]');
    expect(fetchItem).toHaveAttribute("data-state", "completed");
  });

  it("renders the success state and offers a close affordance", async () => {
    const onClose = vi.fn();
    render(
      <ScanProgress
        scanId="scan-1"
        socketFactory={factory}
        onClose={onClose}
      />,
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 100,
        step: "succeeded",
        ts: "2026-05-06T12:00:01.000Z",
      }),
    );
    await waitFor(() => {
      expect(screen.getByText(/Scan completed/i)).toBeInTheDocument();
    });
    const closeBtn = screen.getByTestId("scan-progress-close");
    await userEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders the failed state and shows the retry button when handler is provided", async () => {
    const onRetry = vi.fn();
    render(
      <ScanProgress
        scanId="scan-1"
        socketFactory={factory}
        onRetry={onRetry}
      />,
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 60,
        step: "failed",
        ts: "2026-05-06T12:00:01.000Z",
      }),
    );
    await waitFor(() => {
      expect(screen.getByText(/Scan failed/i)).toBeInTheDocument();
    });
    const retryBtn = screen.getByTestId("scan-progress-retry");
    await userEvent.click(retryBtn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("shows the DT-cached alert when the prop is true", () => {
    render(
      <ScanProgress
        scanId="scan-1"
        socketFactory={factory}
        cachedFromDtDown
      />,
    );
    expect(screen.getByTestId("scan-dt-cached-alert")).toBeInTheDocument();
  });
});
