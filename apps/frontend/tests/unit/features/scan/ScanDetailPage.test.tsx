/**
 * ScanDetailPage — unit tests.
 *
 * Mounts the page with a stubbed `useScanWebSocket` (so we drive `logMessages`
 * deterministically) and a mocked `getScan` for the header. The `fetch`
 * download is stubbed too — we never touch the network. Object-URL APIs are
 * stubbed because jsdom has neither `URL.createObjectURL` nor an anchor
 * `click()` that does anything useful.
 *
 * Coverage:
 *   - Page header (short id, status badge, download button).
 *   - Unified log stream renders all stages in order.
 *   - Filter chips: cdxgen / errors / all.
 *   - Empty state shows the i18n key when logMessages is empty.
 *   - Download button: fetch URL + credentials, anchor.download filename.
 *   - Download 404 surfaces an inline toast.
 *   - Disabled state while queued + no logs; enabled once a line arrives.
 *   - Auto-scroll pin-to-bottom behaviour.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ScanLogMessage,
  ScanWebSocketState,
} from "@/hooks/useScanWebSocket";
import type { ScanPublic, ScanStatus } from "@/lib/projectsApi";

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

// Drive logMessages + status without standing up a real WebSocket. The hook
// is also imported by ScanProgress (the sub-component the page renders); the
// stub returns the same shape so both consumers behave deterministically.
const wsState: {
  logMessages: ScanLogMessage[];
  state: ScanWebSocketState;
} = {
  logMessages: [],
  state: "open",
};

vi.mock("@/hooks/useScanWebSocket", async () => {
  const actual =
    await vi.importActual<typeof import("@/hooks/useScanWebSocket")>(
      "@/hooks/useScanWebSocket",
    );
  return {
    ...actual,
    useScanWebSocket: () => ({
      state: wsState.state,
      lastMessage: null,
      messages: [],
      logMessages: wsState.logMessages,
      closeCode: null,
      closeReason: null,
      reconnectAttempt: 0,
      isTerminal: false,
    }),
  };
});

vi.mock("@/lib/projectsApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/projectsApi")>(
      "@/lib/projectsApi",
    );
  return { ...actual, getScan: vi.fn() };
});

import { getScan } from "@/lib/projectsApi";
import { ScanDetailPage } from "@/features/scan/ScanDetailPage";
const mockedGetScan = vi.mocked(getScan);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SCAN_ID = "abcd1234-1111-2222-3333-444455556666";

function scanFixture(overrides: Partial<ScanPublic> = {}): ScanPublic {
  return {
    id: SCAN_ID,
    project_id: "proj-1",
    kind: "source",
    status: "running" as ScanStatus,
    progress_percent: 50,
    current_step: "cdxgen",
    started_at: "2026-05-28T01:00:00Z",
    completed_at: null,
    error_message: null,
    requested_by_user_id: null,
    celery_task_id: "task-1",
    metadata: {},
    release: "v1.2.3",
    project_name: "alpha",
    created_at: "2026-05-28T00:00:00Z",
    updated_at: "2026-05-28T01:00:00Z",
    ...overrides,
  };
}

function logFixture(
  stage: string,
  stream: "stdout" | "stderr",
  line: string,
  ts: string,
): ScanLogMessage {
  return { type: "log", stage, stream, line, ts };
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

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe("ScanDetailPage", () => {
  let originalCreateObjectURL: typeof URL.createObjectURL | undefined;
  let originalRevokeObjectURL: typeof URL.revokeObjectURL | undefined;
  let originalFetch: typeof fetch | undefined;

  beforeEach(() => {
    wsState.logMessages = [];
    wsState.state = "open";
    mockedGetScan.mockReset();
    mockedGetScan.mockResolvedValue(scanFixture());

    // Stub URL.* for the download path.
    originalCreateObjectURL = URL.createObjectURL;
    originalRevokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => "blob:mock") as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;

    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    URL.createObjectURL = originalCreateObjectURL!;
    URL.revokeObjectURL = originalRevokeObjectURL!;
    if (originalFetch !== undefined) {
      globalThis.fetch = originalFetch;
    }
  });

  // ---- 1. Header ----------------------------------------------------------

  it("renders the page header with short id, status badge, and download button", async () => {
    renderPage();

    // Title: scans:detail.title → "Scan {{shortId}}", short = first 8 chars.
    const title = await screen.findByTestId("scan-detail-page-title");
    expect(title).toHaveTextContent("abcd1234");

    // Status badge mounts once getScan resolves.
    await waitFor(() => {
      expect(screen.getByTestId("scan-detail-page-status")).toBeInTheDocument();
    });

    // Download button is always present (gating is by disabled prop).
    expect(screen.getByTestId("scan-detail-page-download")).toBeInTheDocument();
  });

  // ---- 2 & 3 & 4 & 5. Log stream + filtering ------------------------------

  it("renders unified log stream with all stage messages in order", async () => {
    wsState.logMessages = [
      logFixture("cdxgen", "stdout", "resolving packages", "2026-05-28T01:01:00.000Z"),
      logFixture("scancode", "stdout", "detecting licenses", "2026-05-28T01:02:00.000Z"),
      logFixture("trivy", "stderr", "db refresh required", "2026-05-28T01:03:00.000Z"),
    ];
    renderPage();

    const body = await screen.findByTestId("scan-detail-page-log-body");
    expect(body).toBeInTheDocument();
    expect(body).toHaveTextContent("resolving packages");
    expect(body).toHaveTextContent("detecting licenses");
    expect(body).toHaveTextContent("db refresh required");

    // All three rows mounted.
    const rows = body.querySelectorAll('[data-stage]');
    expect(rows).toHaveLength(3);
    // Order: cdxgen, scancode, trivy.
    expect(rows[0]).toHaveAttribute("data-stage", "cdxgen");
    expect(rows[1]).toHaveAttribute("data-stage", "scancode");
    expect(rows[2]).toHaveAttribute("data-stage", "trivy");
  });

  it("filtering by cdxgen chip narrows the list to only cdxgen rows", async () => {
    wsState.logMessages = [
      logFixture("cdxgen", "stdout", "a", "2026-05-28T01:01:00.000Z"),
      logFixture("scancode", "stdout", "b", "2026-05-28T01:02:00.000Z"),
      logFixture("cdxgen", "stdout", "c", "2026-05-28T01:03:00.000Z"),
    ];
    renderPage();

    await screen.findByTestId("scan-detail-page-log-body");
    await userEvent.click(screen.getByTestId("scan-detail-page-filter-cdxgen"));

    const rows = screen
      .getByTestId("scan-detail-page-log-body")
      .querySelectorAll("[data-stage]");
    expect(rows).toHaveLength(2);
    rows.forEach((r) => expect(r).toHaveAttribute("data-stage", "cdxgen"));
  });

  it("errors-only filter narrows to stderr messages across all stages", async () => {
    wsState.logMessages = [
      logFixture("cdxgen", "stdout", "ok", "2026-05-28T01:01:00.000Z"),
      logFixture("cdxgen", "stderr", "warn", "2026-05-28T01:02:00.000Z"),
      logFixture("trivy", "stderr", "boom", "2026-05-28T01:03:00.000Z"),
      logFixture("scancode", "stdout", "ok", "2026-05-28T01:04:00.000Z"),
    ];
    renderPage();

    await screen.findByTestId("scan-detail-page-log-body");
    await userEvent.click(screen.getByTestId("scan-detail-page-filter-errors"));

    const rows = screen
      .getByTestId("scan-detail-page-log-body")
      .querySelectorAll("[data-stream]");
    expect(rows).toHaveLength(2);
    rows.forEach((r) => expect(r).toHaveAttribute("data-stream", "stderr"));
  });

  it("clicking All restores the full list", async () => {
    wsState.logMessages = [
      logFixture("cdxgen", "stdout", "a", "2026-05-28T01:01:00.000Z"),
      logFixture("trivy", "stderr", "b", "2026-05-28T01:02:00.000Z"),
    ];
    renderPage();

    await screen.findByTestId("scan-detail-page-log-body");
    // Narrow first, then widen.
    await userEvent.click(screen.getByTestId("scan-detail-page-filter-cdxgen"));
    expect(
      screen
        .getByTestId("scan-detail-page-log-body")
        .querySelectorAll("[data-stage]"),
    ).toHaveLength(1);

    await userEvent.click(screen.getByTestId("scan-detail-page-filter-all"));
    expect(
      screen
        .getByTestId("scan-detail-page-log-body")
        .querySelectorAll("[data-stage]"),
    ).toHaveLength(2);
  });

  it("renders the empty state when logMessages is empty", async () => {
    wsState.logMessages = [];
    renderPage();

    const empty = await screen.findByTestId("scan-detail-page-log-empty");
    // i18n bundle is the real EN one — assert the literal string for the
    // `detail.empty` key without coupling to the JSON contents.
    expect(empty).toBeInTheDocument();
    expect(empty.textContent ?? "").toMatch(/waiting/i);
  });

  // ---- 6 & 7 & 8. Download path ------------------------------------------

  it("download button issues GET /api/v1/scans/<id>/log with credentials and triggers an anchor download", async () => {
    wsState.logMessages = [
      logFixture("cdxgen", "stdout", "a", "2026-05-28T01:01:00.000Z"),
    ];
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(new Blob(["log body"]), {
        status: 200,
        headers: { "content-type": "text/plain" },
      }),
    );
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    // Capture the anchor that handleDownload synthesises to inspect its
    // `download` attribute.
    const realCreate = document.createElement.bind(document);
    const anchors: HTMLAnchorElement[] = [];
    const createSpy = vi.spyOn(document, "createElement").mockImplementation(
      (tag: string, opts?: ElementCreationOptions) => {
        const el = realCreate(tag, opts) as HTMLElement;
        if (tag === "a") {
          anchors.push(el as HTMLAnchorElement);
        }
        return el as HTMLElement;
      },
    );

    renderPage();
    await screen.findByTestId("scan-detail-page-log-body");

    await userEvent.click(screen.getByTestId("scan-detail-page-download"));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe(`/api/v1/scans/${SCAN_ID}/log`);
    expect((init as RequestInit).credentials).toBe("include");

    // The anchor's `download` attribute is set to `scan-<id>.log`.
    const downloadAnchor = anchors.find((a) => a.download);
    expect(downloadAnchor).toBeDefined();
    expect(downloadAnchor!.download).toBe(`scan-${SCAN_ID}.log`);

    createSpy.mockRestore();
  });

  it("a 404 response surfaces a destructive toast", async () => {
    wsState.logMessages = [
      logFixture("cdxgen", "stdout", "a", "2026-05-28T01:01:00.000Z"),
    ];
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response("not found", { status: 404 }),
    ) as unknown as typeof fetch;

    renderPage();
    await screen.findByTestId("scan-detail-page-log-body");

    await userEvent.click(screen.getByTestId("scan-detail-page-download"));

    const toast = await screen.findByTestId("scan-detail-page-toast");
    expect(toast).toHaveAttribute("data-toast-variant", "destructive");
    expect(toast.textContent ?? "").toMatch(/not available/i);
  });

  // ---- 9. Disabled state --------------------------------------------------

  it("download button is disabled when status=queued and no logs have arrived", async () => {
    mockedGetScan.mockReset();
    mockedGetScan.mockResolvedValue(scanFixture({ status: "queued" }));
    wsState.logMessages = [];
    renderPage();

    // Wait for the query to resolve so the live status flows in.
    await waitFor(() => {
      expect(mockedGetScan).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByTestId("scan-detail-page-download")).toBeDisabled();
    });
  });

  it("download button enables once a log line arrives", async () => {
    mockedGetScan.mockReset();
    mockedGetScan.mockResolvedValue(scanFixture({ status: "queued" }));
    wsState.logMessages = [
      logFixture("cdxgen", "stdout", "first", "2026-05-28T01:01:00.000Z"),
    ];
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("scan-detail-page-download")).not.toBeDisabled();
    });
  });

  // ---- 10. Auto-scroll pin behaviour --------------------------------------

  it("auto-scroll pins to bottom on new messages and unpins when the user scrolls up", async () => {
    wsState.logMessages = [
      logFixture("cdxgen", "stdout", "a", "2026-05-28T01:01:00.000Z"),
    ];
    renderPage();

    const log = await screen.findByTestId("scan-detail-page-log");
    // Default is pinned.
    expect(log).toHaveAttribute("data-pinned-bottom", "true");

    // Simulate the user scrolling up: scrollTop = 0 with non-zero scrollHeight.
    Object.defineProperty(log, "scrollTop", { value: 0, configurable: true, writable: true });
    Object.defineProperty(log, "scrollHeight", { value: 1000, configurable: true, writable: true });
    Object.defineProperty(log, "clientHeight", { value: 200, configurable: true, writable: true });

    act(() => {
      log.dispatchEvent(new Event("scroll"));
    });

    await waitFor(() => {
      expect(log).toHaveAttribute("data-pinned-bottom", "false");
    });

    // Now scroll back to the bottom: scrollTop + clientHeight >= scrollHeight - 10.
    Object.defineProperty(log, "scrollTop", { value: 800, configurable: true, writable: true });
    act(() => {
      log.dispatchEvent(new Event("scroll"));
    });
    await waitFor(() => {
      expect(log).toHaveAttribute("data-pinned-bottom", "true");
    });
  });
});
