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

  /** A close the component did not ask for, with a code the server sends. */
  __closeFromServer(code: number, reason = "") {
    this.readyState = 3;
    if (this.onclose) {
      this.onclose({ code, reason, wasClean: false } as CloseEvent);
    }
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

  // L-23 — the bar's accessible name must follow the terminal state so a
  // screen reader never keeps announcing "Scan in progress" on a dead scan.
  it("updates the progressbar accessible name on a failed terminal frame (L-23)", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 30,
        step: "sbom",
        ts: "2026-05-06T12:00:00.000Z",
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("scan-progress-bar")).toHaveAttribute(
        "aria-label",
        expect.stringMatching(/in progress/i),
      );
    });
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 60,
        step: "failed",
        ts: "2026-05-06T12:00:01.000Z",
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("scan-progress-bar")).toHaveAttribute(
        "aria-label",
        expect.stringMatching(/failed/i),
      );
    });
  });

  it("updates the progressbar accessible name on success (L-23)", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 100,
        step: "succeeded",
        ts: "2026-05-06T12:00:01.000Z",
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("scan-progress-bar")).toHaveAttribute(
        "aria-label",
        expect.stringMatching(/(succeeded|complete)/i),
      );
    });
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

  // ---------------------------------------------------------------------
  // hideInlineLog + trivy stage label (scan-detail-page-fe-v2 followups)
  // ---------------------------------------------------------------------

  it("hideInlineLog suppresses the embedded tool-log panel", async () => {
    // With `hideInlineLog`, even a log frame that would normally surface
    // the toggle MUST NOT render the panel — the dedicated /scans/:scanId
    // page owns the log surface for the drawer call sites.
    renderProgress(
      <ScanProgress scanId="scan-1" socketFactory={factory} hideInlineLog />,
    );
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        type: "log",
        stage: "cdxgen",
        stream: "stdout",
        line: "would normally show toggle",
        ts: "2026-05-28T12:00:00.000Z",
      }),
    );

    // The toggle would mount inside `scan-progress-log` if hideInlineLog
    // were false — assert neither node is in the DOM.
    expect(
      screen.queryByTestId("scan-progress-log"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("scan-progress-log-toggle"),
    ).not.toBeInTheDocument();
  });

  it("PIPELINE_STEPS surfaces the Trivy label when the trivy step is current", async () => {
    // The IA replaces dt_findings with trivy; the user-facing step row must
    // render the localised "Trivy (CVE match)" label for that slot.
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());
    act(() =>
      FakeSocket.instances[0].__message({
        percent: 70,
        step: "trivy",
        ts: "2026-05-28T12:00:00.000Z",
      }),
    );

    const stepItem = await waitFor(() => {
      const node = screen
        .getByTestId("scan-progress-steps")
        .querySelector('[data-step="trivy"]');
      if (node == null) throw new Error("trivy step not mounted");
      return node;
    });
    expect(stepItem).toHaveAttribute("data-state", "current");
    // i18n EN label for `progress.step_trivy` is "Trivy (CVE match)".
    expect(stepItem.textContent ?? "").toMatch(/Trivy/i);
  });

  // ---------------------------------------------------------------------
  // C4 - what the panel says once the stream has stopped for good.
  //
  // Until this unit the component had no branch for it at all: the hook
  // flipped to a state nothing read, and the panel went on saying
  // "Reconnecting... Attempt 14" for as long as the page stayed open. None
  // of the reconnect display had component coverage either.
  // ---------------------------------------------------------------------

  it("stops claiming to reconnect once the stream has given up", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());

    // An eviction: the account's connection cap took this socket away.
    act(() => FakeSocket.instances[0].__closeFromServer(1001, "newer_connection"));

    const stopped = await screen.findByTestId("scan-progress-stopped");
    expect(screen.queryByTestId("scan-progress-reconnecting")).toBeNull();
    // The reader's first question is whether their scan died with the
    // connection, so the answer is in the panel every time.
    expect(stopped.textContent).toContain("still running");
  });

  it("names the reason the stream stopped, per close code", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());

    act(() => FakeSocket.instances[0].__closeFromServer(1001, "newer_connection"));

    const stopped = await screen.findByTestId("scan-progress-stopped");
    expect(stopped.dataset.closeCode).toBe("1001");
    // Specific to 1001, not the generic network sentence: this one is a
    // decision the server made, and telling the reader their network dropped
    // would send them to debug the wrong thing.
    expect(stopped.textContent).toContain("Another tab");
  });

  // Every close code the panel can render, with the whole sentence it puts on
  // screen. The unit this belongs to is judged on whether that copy is true
  // of the code, so the copy is what is asserted - not a testid, and not a
  // substring that would survive the two halves being recombined wrongly.
  //
  // `unaffected` is the half that has to be conditional. It reads as
  // reassurance and it is right for a stream that failed, but "there is no
  // scan with this id, the scan itself is unaffected and is still running"
  // was on screen for 4404 until it was made per-reason.
  const STOPPED_COPY: [number, string][] = [
    [
      1001,
      "Another tab took over the live connection for this account. " +
        "The scan itself is unaffected and is still running.",
    ],
    [
      4400,
      "The server did not accept this connection. " +
        "The scan itself is unaffected and is still running.",
    ],
    [4403, "You do not have access to this scan."],
    [4404, "There is no scan with this id."],
  ];

  it.each(STOPPED_COPY)(
    "says something true of close code %i",
    async (code, expected) => {
      renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
      act(() => FakeSocket.instances[0].__open());

      act(() => FakeSocket.instances[0].__closeFromServer(code));

      const stopped = await screen.findByTestId("scan-progress-stopped");
      expect(stopped.dataset.closeCode).toBe(String(code));
      expect(stopped.textContent).toContain(expected);
      // The two codes that mean "not your scan" must not carry the
      // reassurance, and the reassurance must not appear twice.
      const reassurances = (
        stopped.textContent?.match(/is still running/g) ?? []
      ).length;
      expect(reassurances).toBe(expected.includes("still running") ? 1 : 0);
    },
  );

  it.each([
    // 1011 gets no reassurance. The gateway subscribes to the same Redis the
    // Celery broker uses, so a Redis failure produces this code AND stops the
    // scan; by the time five minutes of it have passed, "still running" is
    // more likely false than true.
    [1011, "The server ended the stream.", false],
    // 1006 is the browser's own code for "no close frame arrived", which is
    // what nearly every real disconnection looks like. Nothing maps it, so
    // this is the fallback path, and it has to describe a failed connection
    // rather than a decision the server made. The scan is genuinely fine.
    [1006, "The connection dropped and could not be re-established.", true],
  ])(
    "says something true of close code %i, which is reached only by running out of budget",
    async (code, expected, reassures) => {
      // These two are retried, so the panel appears only after the whole
      // five-minute budget is spent. Walking the backoff ladder is the only
      // way there: the budget is checked when a reconnect is scheduled, so
      // jumping the clock in one go skips every check.
      vi.useFakeTimers();
      renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
      act(() => FakeSocket.instances[0].__open());

      const ladder = [1_000, 2_000, 4_000, 8_000, 30_000];
      for (let round = 0; round < 30; round += 1) {
        const socket = FakeSocket.instances[FakeSocket.instances.length - 1];
        act(() => socket.__closeFromServer(code));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(
            ladder[Math.min(round, ladder.length - 1)] + 1,
          );
        });
        if (FakeSocket.instances.length === round + 1) break;
      }

      const stopped = screen.getByTestId("scan-progress-stopped");
      expect(stopped.dataset.closeCode).toBe(String(code));
      expect(stopped.textContent).toContain(expected);
      expect(stopped.textContent?.includes("is still running")).toBe(reassures);
    },
  );

  it("does not repeat the background notice under the stopped panel", async () => {
    // The panel says the scan keeps running; the generic notice below said it
    // again, and for 4403/4404 it contradicted the panel outright.
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());

    act(() => FakeSocket.instances[0].__closeFromServer(4404));

    const panel = await screen.findByTestId("scan-progress");
    expect(panel.textContent).not.toContain("continues in the background");
  });

  it("offers no Reconnect where reconnecting would repeat the refusal", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());

    // 4403: the reader is not in the team that owns this scan. Trying again
    // asks the same question and gets the same answer.
    act(() => FakeSocket.instances[0].__closeFromServer(4403, "forbidden"));

    await screen.findByTestId("scan-progress-stopped");
    expect(screen.queryByTestId("scan-progress-reconnect")).toBeNull();
  });

  it("opens a new socket when the reader presses Reconnect", async () => {
    const user = userEvent.setup();
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());
    act(() => FakeSocket.instances[0].__closeFromServer(1001, "newer_connection"));
    await screen.findByTestId("scan-progress-stopped");

    await user.click(screen.getByTestId("scan-progress-reconnect"));

    expect(FakeSocket.instances).toHaveLength(2);
    await waitFor(() => {
      expect(screen.queryByTestId("scan-progress-stopped")).toBeNull();
    });
  });

  it("says it is reconnecting only while it is", async () => {
    renderProgress(<ScanProgress scanId="scan-1" socketFactory={factory} />);
    act(() => FakeSocket.instances[0].__open());

    // 1011 is retried, so this is the state the old copy was written for.
    act(() => FakeSocket.instances[0].__closeFromServer(1011, "internal"));

    expect(
      await screen.findByTestId("scan-progress-reconnecting"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("scan-progress-stopped")).toBeNull();
  });
});
