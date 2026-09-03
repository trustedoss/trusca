/**
 * ScanDetailPage liveness (ER40).
 *
 * The page gates its Download button on the scan still being `queued`. Before
 * this guard the only thing that could lift that gate was a WebSocket frame:
 * the scan query had no polling and nothing in the app invalidated it, so a
 * socket that never delivered left a finished scan showing a permanently
 * disabled button. That is reachable in production (the hook does not
 * auto-reconnect after a 4429 capacity close) and it is what made the nightly
 * e2e `scan_detail_page` spec fail on six consecutive nights.
 *
 * These tests drive the page with a socket that says nothing at all, which is
 * the condition the old code could not recover from.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ScanDetailPage } from "@/features/scan/ScanDetailPage";
import type { ScanPublic } from "@/lib/projectsApi";

vi.mock("@/lib/projectsApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/projectsApi")>(
      "@/lib/projectsApi",
    );
  return { ...actual, getScan: vi.fn() };
});

// A socket that never speaks. `capturedOptions` lets the second test check
// that the page actually wires the non-terminal-close fallback rather than
// only that the fallback exists in the hook.
const silentSocket = {
  state: "open" as const,
  lastMessage: null,
  messages: [],
  logMessages: [],
  closeCode: null,
  closeReason: null,
  reconnectAttempt: 0,
  gaveUp: false,
  reconnect: vi.fn(),
  isTerminal: false,
};
// Every render appends, so a test reads what THIS render passed rather than
// whatever a previous test happened to leave behind.
let capturedOptions: Record<string, unknown>[] = [];

vi.mock("@/hooks/useScanWebSocket", async () => {
  const actual =
    await vi.importActual<typeof import("@/hooks/useScanWebSocket")>(
      "@/hooks/useScanWebSocket",
    );
  return {
    ...actual,
    useScanWebSocket: (_scanId: string, options?: Record<string, unknown>) => {
      capturedOptions.push(options ?? {});
      return silentSocket;
    },
  };
});

// ScanProgress calls the same hook AND wires its own onNonTerminalClose, so
// leaving it in makes the capture below read the drawer's options instead of
// the page's. It has its own tests.
vi.mock("@/features/scan/ScanProgress", () => ({ ScanProgress: () => null }));

// Side panels run their own queries and are not what these tests are about.
vi.mock("@/features/scan/ScanProvenancePanel", () => ({
  ScanProvenancePanel: () => null,
}));
vi.mock("@/features/scan/SbomConformancePanel", () => ({
  SbomConformancePanel: () => null,
}));
vi.mock("@/features/scan/OsEolPanel", () => ({ OsEolPanel: () => null }));

import { getScan } from "@/lib/projectsApi";

const mockedGetScan = vi.mocked(getScan);

const SCAN_ID = "11111111-2222-3333-4444-555555555555";
// Mirrors SCAN_DETAIL_POLL_MS in the page. Kept local so the test states the
// budget it relies on rather than importing an implementation detail.
const SCAN_DETAIL_POLL_MS = 4000;

function scan(status: ScanPublic["status"]): ScanPublic {
  return {
    id: SCAN_ID,
    project_id: "99999999-8888-7777-6666-555555555555",
    kind: "source",
    status,
    progress_percent: status === "queued" ? 0 : 100,
    current_step: status === "queued" ? "" : "succeeded",
    started_at: null,
    completed_at: null,
    error_message: null,
    requested_by_user_id: null,
    celery_task_id: null,
    metadata: {},
  } as unknown as ScanPublic;
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/scans/${SCAN_ID}`]}>
        <Routes>
          <Route path="/scans/:scanId" element={<ScanDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ScanDetailPage liveness without a WebSocket", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capturedOptions = [];
  });

  it("polls a queued scan and enables Download once it finishes", async () => {
    // Queued first, then finished. Nothing arrives over the socket, so a poll
    // is the only way the page can learn about the second value.
    mockedGetScan
      .mockResolvedValueOnce(scan("queued"))
      .mockResolvedValue(scan("succeeded"));

    renderPage();

    const button = await screen.findByTestId("scan-detail-page-download");
    expect(button).toBeDisabled();

    // Real timers with a generous window: the page polls every few seconds and
    // faking timers here fights TanStack's own scheduling.
    await waitFor(() => expect(mockedGetScan.mock.calls.length).toBeGreaterThan(1), {
      timeout: 15_000,
    });
    await waitFor(
      () => expect(screen.getByTestId("scan-detail-page-download")).toBeEnabled(),
      { timeout: 15_000 },
    );
  }, 20_000);

  it("stops polling once the scan is terminal", async () => {
    mockedGetScan.mockResolvedValue(scan("succeeded"));

    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("scan-detail-page-download")).toBeEnabled(),
    );

    const afterFirstRender = mockedGetScan.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 6000));
    // A finished scan has nothing left to report, so an idle page must not
    // keep asking.
    expect(mockedGetScan.mock.calls.length).toBe(afterFirstRender);
  }, 20_000);

  it("re-reads the scan when the socket closes without a terminal frame", async () => {
    mockedGetScan.mockResolvedValue(scan("queued"));
    renderPage();
    await screen.findByTestId("scan-detail-page-download");

    // The page must hand the hook a fallback; the drawer does, and this page
    // did not, which is half of why it could get stuck.
    expect(capturedOptions.length).toBeGreaterThan(0);
    const options = capturedOptions[capturedOptions.length - 1];
    expect(typeof options.onNonTerminalClose).toBe("function");

    // Calling it must issue a read immediately. The assertion window is well
    // under the poll interval, so a passing run cannot be the poll firing:
    // this proves the fallback itself is connected. That the fresh status then
    // reaches the button is covered by the polling test above.
    const before = mockedGetScan.mock.calls.length;
    (options.onNonTerminalClose as (code: number) => void)(1006);

    await waitFor(
      () => expect(mockedGetScan.mock.calls.length).toBeGreaterThan(before),
      { timeout: SCAN_DETAIL_POLL_MS / 2 },
    );
  }, 20_000);
});
