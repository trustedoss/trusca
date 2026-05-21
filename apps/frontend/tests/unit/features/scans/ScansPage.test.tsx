/**
 * ScansPage — unit tests (Step 4-C).
 *
 * Coverage targets:
 *   - Initial render queries with the running tab's status filter.
 *   - Switching tabs (queued / failed / all) re-queries with the matching
 *     status filter; "all" sends `status: undefined` so the backend returns
 *     every status.
 *   - Empty state renders when the page has no rows.
 *   - Rows render with project_id prefix, kind, status badge, and duration.
 *   - Pagination Next/Previous buttons disabled at the boundaries.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ScansPage } from "@/features/scans/ScansPage";
import type { ScanListResponse, ScanPublic } from "@/lib/projectsApi";

vi.mock("@/lib/projectsApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/projectsApi")
  >("@/lib/projectsApi");
  return {
    ...actual,
    listMyScans: vi.fn(),
  };
});

import { listMyScans } from "@/lib/projectsApi";
const mockedListMyScans = vi.mocked(listMyScans);

function scanFixture(overrides: Partial<ScanPublic> = {}): ScanPublic {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    project_id: "abcdef12-3456-7890-abcd-ef1234567890",
    kind: "source",
    status: "running",
    progress_percent: 50,
    current_step: null,
    started_at: "2026-05-08T00:00:00Z",
    completed_at: null,
    error_message: null,
    requested_by_user_id: null,
    celery_task_id: null,
    metadata: {},
    created_at: "2026-05-08T00:00:00Z",
    updated_at: "2026-05-08T00:00:00Z",
    ...overrides,
  };
}

function pageResponse(items: ScanPublic[], total = items.length): ScanListResponse {
  return { items, total, page: 1, size: 20 };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ScansPage />
    </QueryClientProvider>,
  );
}

describe("ScansPage", () => {
  beforeEach(() => {
    mockedListMyScans.mockReset();
  });

  it("renders the page and queries with the running tab's status filter", async () => {
    mockedListMyScans.mockResolvedValue(pageResponse([scanFixture()]));
    renderPage();
    expect(screen.getByTestId("scans-page")).toBeInTheDocument();
    await waitFor(() => {
      expect(mockedListMyScans).toHaveBeenCalled();
    });
    expect(mockedListMyScans.mock.calls[0]?.[0]).toMatchObject({
      status: "running",
    });
  });

  it("switching to the failed tab re-queries with status=failed", async () => {
    mockedListMyScans.mockResolvedValue(pageResponse([]));
    renderPage();
    await waitFor(() => {
      expect(mockedListMyScans).toHaveBeenCalled();
    });
    await userEvent.click(screen.getByTestId("scans-tab-failed"));
    await waitFor(() => {
      const last = mockedListMyScans.mock.calls.at(-1)?.[0];
      expect(last).toMatchObject({ status: "failed", page: 1 });
    });
  });

  it("the All tab clears the status filter (sends undefined)", async () => {
    mockedListMyScans.mockResolvedValue(pageResponse([]));
    renderPage();
    await waitFor(() => {
      expect(mockedListMyScans).toHaveBeenCalled();
    });
    await userEvent.click(screen.getByTestId("scans-tab-all"));
    await waitFor(() => {
      const last = mockedListMyScans.mock.calls.at(-1)?.[0];
      expect(last?.status).toBeUndefined();
    });
  });

  it("renders the empty state when no rows match", async () => {
    mockedListMyScans.mockResolvedValue(pageResponse([]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("scans-empty")).toBeInTheDocument();
    });
  });

  it("renders one row per scan with project prefix and status badge", async () => {
    mockedListMyScans.mockResolvedValue(
      pageResponse([
        scanFixture({ id: "scan-a", status: "running" }),
        scanFixture({
          id: "scan-b",
          project_id: "fedcba98-7654-3210-fedc-ba9876543210",
          status: "succeeded",
          completed_at: "2026-05-08T00:01:00Z",
        }),
      ]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("scans-row")).toHaveLength(2);
    });
    const rows = screen.getAllByTestId("scans-row");
    expect(rows[0]).toHaveAttribute("data-status", "running");
    expect(rows[1]).toHaveAttribute("data-status", "succeeded");
    // Project column shows the first 8 chars of the project_id.
    expect(rows[0]?.textContent).toContain("abcdef12");
    expect(rows[1]?.textContent).toContain("fedcba98");
  });

  it("shows the cancel affordance only for queued/running rows (PR-A3)", async () => {
    mockedListMyScans.mockResolvedValue(
      pageResponse([
        scanFixture({ id: "scan-a", status: "running" }),
        scanFixture({
          id: "scan-b",
          project_id: "fedcba98-7654-3210-fedc-ba9876543210",
          status: "succeeded",
          completed_at: "2026-05-08T00:01:00Z",
        }),
      ]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("scans-row")).toHaveLength(2);
    });
    // Exactly one cancel button — the running row. The succeeded row has none.
    const cancelButtons = screen.getAllByTestId("scan-cancel-button");
    expect(cancelButtons).toHaveLength(1);
    expect(cancelButtons[0]).toHaveAttribute("data-scan-id", "scan-a");
  });

  it("pagination Previous is disabled on page 1; Next is disabled when total fits one page", async () => {
    mockedListMyScans.mockResolvedValue(pageResponse([scanFixture()], 1));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("scans-row")).toBeInTheDocument();
    });
    expect(screen.getByTestId("scans-page-prev")).toBeDisabled();
    expect(screen.getByTestId("scans-page-next")).toBeDisabled();
  });

  it("Next button advances the page when there are more results", async () => {
    mockedListMyScans.mockResolvedValue(pageResponse([scanFixture()], 50));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("scans-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("scans-page-next"));
    await waitFor(() => {
      const last = mockedListMyScans.mock.calls.at(-1)?.[0];
      expect(last?.page).toBe(2);
    });
  });
});
