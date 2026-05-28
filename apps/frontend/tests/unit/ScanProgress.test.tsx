/**
 * ScanProgress — unit tests (PR #9 task 2.10).
 *
 * The component reads from `useScanWebSocket`, which we drive via the
 * `socketFactory` injection seam. No real WebSocket is created.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ScanProgress } from "@/features/scan/ScanProgress";
import { useAuthStore } from "@/stores/authStore";

// BUG-007: ScanProgress refetches the scan status (`getScan`) after a
// non-terminal socket close to detect a cancellation the backend never
// published over WS. Mock the wire call so tests can drive that path.
vi.mock("@/lib/projectsApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/projectsApi")>(
      "@/lib/projectsApi",
    );
  return { ...actual, getScan: vi.fn() };
});

import { getScan } from "@/lib/projectsApi";

const mockedGetScan = vi.mocked(getScan);

/**
 * ScanProgress now renders `ScanCancelButton` (PR-A3) for in-progress scans,
 * which calls `useQueryClient`. Wrap every render in a provider so the cancel
 * affordance can mount.
 */
function renderProgress(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

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
    mockedGetScan.mockReset();
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

  it("renders the title and the 6-step pipeline list", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    expect(screen.getByTestId("scan-progress")).toBeInTheDocument();
    const steps = screen.getByTestId("scan-progress-steps");
    expect(steps.querySelectorAll("[data-step]")).toHaveLength(6);
  });

  it("shows skeleton during connecting state", () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    expect(
      screen.getByTestId("scan-progress-skeleton"),
    ).toBeInTheDocument();
  });

  it("renders progress and marks the current step on incoming frame", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
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
    renderProgress(
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
    renderProgress(
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
    renderProgress(
      <ScanProgress
        scanId="scan-1"
        socketFactory={factory}
        cachedFromDtDown
      />,
    );
    expect(screen.getByTestId("scan-dt-cached-alert")).toBeInTheDocument();
  });

  it("offers the cancel affordance for a running scan (PR-A3)", () => {
    renderProgress(
      <ScanProgress scanId="scan-1" socketFactory={factory} status="running" />,
    );
    expect(screen.getByTestId("scan-cancel-button")).toBeInTheDocument();
  });

  it("hides the cancel affordance once a terminal frame arrives (PR-A3)", async () => {
    renderProgress(
      <ScanProgress scanId="scan-1" socketFactory={factory} status="running" />,
    );
    expect(screen.getByTestId("scan-cancel-button")).toBeInTheDocument();
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 100,
        step: "succeeded",
        ts: "2026-05-06T12:00:02.000Z",
      }),
    );
    await waitFor(() => {
      expect(screen.queryByTestId("scan-cancel-button")).not.toBeInTheDocument();
    });
  });

  // ---- BUG-007: cancelled-state handling ---------------------------------

  it("renders the cancelled terminal state on a cancelled WS frame (BUG-007)", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 90,
        step: "cancelled",
        ts: "2026-05-24T12:00:00.000Z",
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("scan-progress-cancelled")).toBeInTheDocument();
    });
    // Title flips to the cancelled label, the bar carries the cancelled marker,
    // and the "continues in the background" notice is gone.
    expect(screen.getByText(/Scan cancelled/i)).toBeInTheDocument();
    expect(screen.getByTestId("scan-progress-bar")).toHaveAttribute(
      "data-cancelled",
      "true",
    );
  });

  it("renders the cancelled state when the parent passes status='cancelled' (BUG-007)", () => {
    // The cancel button confirmed and the parent flipped status to cancelled
    // before any WS frame; the panel must reflect that immediately.
    renderProgress(
      <ScanProgress
        scanId="scan-1"
        socketFactory={factory}
        status="cancelled"
      />,
    );
    expect(screen.getByTestId("scan-progress-cancelled")).toBeInTheDocument();
    // Cancel affordance must not be offered for a cancelled scan.
    expect(screen.queryByTestId("scan-cancel-button")).not.toBeInTheDocument();
  });

  it("refetches the scan status on a non-terminal close and reflects cancelled (BUG-007)", async () => {
    // Backend cancel path closes the socket WITHOUT a `cancelled` frame; the
    // fallback refetch resolves to status='cancelled' and the panel updates.
    mockedGetScan.mockResolvedValueOnce({
      id: "scan-1",
      project_id: "p1",
      kind: "source",
      status: "cancelled",
      progress_percent: 90,
      current_step: "trivy",
      started_at: null,
      completed_at: null,
      error_message: "Cancelled by user",
      requested_by_user_id: null,
      celery_task_id: null,
      metadata: {},
      release: null,
      created_at: "2026-05-24T12:00:00.000Z",
      updated_at: "2026-05-24T12:00:05.000Z",
    });
    renderProgress(
      <ScanProgress scanId="scan-1" socketFactory={factory} status="running" />,
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 90,
        step: "trivy",
        ts: "2026-05-24T12:00:00.000Z",
      }),
    );
    // Stream drops without a terminal frame (server-side cancel).
    act(() => FakeSocket.instances[0].close(1011, "internal"));

    await waitFor(() => {
      expect(mockedGetScan).toHaveBeenCalledWith("scan-1");
    });
    await waitFor(() => {
      expect(screen.getByTestId("scan-progress-cancelled")).toBeInTheDocument();
    });
  });

  // ---- P1 #11: re-opening a completed scan's drawer --------------------

  it("renders the success state when parent passes status='succeeded' and WS reports a pre-terminal step", async () => {
    // The Recent Scans table re-opens this drawer for a finished scan. The
    // BE rewrites the initial sync step to the terminal verdict, but even if
    // the SPA ever sees a stale `step="finalize"` (older worker write, retry,
    // etc.) it must trust the `status` prop and flip to the success branch
    // rather than render an animated spinner on a step that is done.
    renderProgress(
      <ScanProgress
        scanId="scan-1"
        socketFactory={factory}
        status="succeeded"
      />,
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 95,
        step: "finalize",
        ts: "2026-05-26T12:00:00.000Z",
      }),
    );

    // Title flips to the success label.
    await waitFor(() => {
      expect(screen.getByText(/Scan completed/i)).toBeInTheDocument();
    });
    // The `finalize` row is NOT showing the "current" spinner — it should be
    // marked completed because the scan as a whole succeeded.
    const finalizeItem = screen
      .getByTestId("scan-progress-steps")
      .querySelector('[data-step="finalize"]');
    expect(finalizeItem).not.toHaveAttribute("data-state", "current");
    // No cancel affordance for a terminal scan.
    expect(screen.queryByTestId("scan-cancel-button")).not.toBeInTheDocument();
  });

  it("renders the failed state when parent passes status='failed' even with a pre-terminal WS step", async () => {
    renderProgress(
      <ScanProgress
        scanId="scan-1"
        socketFactory={factory}
        status="failed"
      />,
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 50,
        step: "trivy",
        ts: "2026-05-26T12:00:00.000Z",
      }),
    );
    await waitFor(() => {
      expect(screen.getByText(/Scan failed/i)).toBeInTheDocument();
    });
    const stepItem = screen
      .getByTestId("scan-progress-steps")
      .querySelector('[data-step="trivy"]');
    expect(stepItem).not.toHaveAttribute("data-state", "current");
  });

  // ---------------------------------------------------------------------
  // P2 #8c — tool log panel (cdxgen / scancode stdout / stderr streaming)
  // ---------------------------------------------------------------------

  it("renders the tool log panel when a log frame arrives", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
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

    // The toggle button surfaces once the first frame lands.
    const toggle = await screen.findByTestId("scan-progress-log-toggle");
    expect(toggle.textContent).toMatch(/Tool log/i);

    // Expand and assert the line renders with the stage badge + content.
    await userEvent.click(toggle);
    const body = await screen.findByTestId("scan-progress-log-body");
    const row = body.querySelector('[data-stage="cdxgen"]');
    expect(row).not.toBeNull();
    expect(row).toHaveAttribute("data-stream", "stdout");
    expect(row?.textContent).toMatch(/resolving package tree/);
  });

  it("tints stderr lines and shows the err badge", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        type: "log",
        stage: "scancode",
        stream: "stderr",
        line: "warning: licenseref unknown",
        ts: "2026-05-26T12:00:00.000Z",
      }),
    );

    const toggle = await screen.findByTestId("scan-progress-log-toggle");
    await userEvent.click(toggle);
    const body = await screen.findByTestId("scan-progress-log-body");
    const row = body.querySelector('[data-stream="stderr"]');
    expect(row).not.toBeNull();
    // The err badge is rendered with an explicit aria-label.
    expect(row?.querySelector('[aria-label="stderr"]')).not.toBeNull();
  });

  it("falls back to the per-step progress log when no tool lines have arrived", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());
    // Only a progress frame — no tool stdout yet.
    act(() =>
      FakeSocket.instances[0].__message({
        type: "progress",
        percent: 18,
        step: "prep",
        ts: "2026-05-26T12:00:00.000Z",
      }),
    );

    const toggle = await screen.findByTestId("scan-progress-log-toggle");
    expect(toggle.textContent).toMatch(/Per-step log/i);
  });

  it("interleaves multiple tool log frames in arrival order", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        type: "log",
        stage: "cdxgen",
        stream: "stdout",
        line: "line1",
        ts: "2026-05-26T12:00:00.000Z",
      }),
    );
    act(() =>
      FakeSocket.instances[0].__message({
        type: "log",
        stage: "scancode",
        stream: "stdout",
        line: "line2",
        ts: "2026-05-26T12:00:01.000Z",
      }),
    );
    const toggle = await screen.findByTestId("scan-progress-log-toggle");
    await userEvent.click(toggle);
    const body = await screen.findByTestId("scan-progress-log-body");
    const rows = body.querySelectorAll("li");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toMatch(/line1/);
    expect(rows[1].textContent).toMatch(/line2/);
  });
});
