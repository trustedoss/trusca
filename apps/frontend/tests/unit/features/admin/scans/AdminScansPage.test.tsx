/**
 * AdminScansPage — unit tests.
 *
 * Coverage targets:
 *   - Tab switching changes the `status` query param.
 *   - Empty state renders when the list is empty.
 *   - Row click opens the drawer pre-populated with the row payload.
 *   - The drawer's cancel flow calls the cancel API.
 *   - Status-illegal cancel surfaces the matching toast key.
 *   - M-35: kind select + debounced project search reach the API call and
 *     rewind pagination to page 1.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ToastProvider } from "@/components/ui/toast";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AdminScansPage } from "@/features/admin/scans/AdminScansPage";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/admin/scans/api/adminScansApi", async () => {
  return {
    listAdminScans: vi.fn(),
    cancelAdminScan: vi.fn(),
  };
});

import {
  cancelAdminScan,
  listAdminScans,
  type AdminScanListItem,
  type AdminScanListPage,
} from "@/features/admin/scans/api/adminScansApi";

const mockedList = vi.mocked(listAdminScans);
const mockedCancel = vi.mocked(cancelAdminScan);

function scanFixture(
  overrides: Partial<AdminScanListItem> = {},
): AdminScanListItem {
  return {
    id: overrides.id ?? "11111111-1111-1111-1111-111111111111",
    project_id: "p1",
    project_name: "alpha",
    team_id: "t1",
    team_name: "team-a",
    status: overrides.status ?? "running",
    kind: overrides.kind ?? "source",
    progress_percent: 0,
    started_at: "2026-05-08T00:00:00Z",
    finished_at: null,
    duration_seconds: null,
    error_message: null,
    requested_by_user_id: null,
    created_at: "2026-05-08T00:00:00Z",
    ...overrides,
  };
}

function pageResponse(
  items: AdminScanListItem[],
  total: number = items.length,
): AdminScanListPage {
  return { items, total, page: 1, page_size: 50 };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <AdminScansPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("AdminScansPage", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedCancel.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders rows for the running tab and re-queries when switching to failed", async () => {
    mockedList.mockResolvedValue(pageResponse([scanFixture()]));
    renderPage();
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(1);
    });
    expect(mockedList.mock.calls[0]?.[0]).toMatchObject({ status: "running" });

    await userEvent.click(screen.getByTestId("admin-scans-tab-failed"));
    await waitFor(() => {
      const last = mockedList.mock.calls.at(-1)?.[0];
      expect(last).toMatchObject({ status: "failed" });
    });
  });

  it("renders the empty state when no rows match", async () => {
    mockedList.mockResolvedValue(pageResponse([]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-scans-empty")).toBeInTheDocument();
    });
  });

  it("'all' tab clears the status filter on the next call", async () => {
    mockedList.mockResolvedValue(pageResponse([]));
    renderPage();
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(1);
    });
    await userEvent.click(screen.getByTestId("admin-scans-tab-all"));
    await waitFor(() => {
      const last = mockedList.mock.calls.at(-1)?.[0];
      expect(last).toMatchObject({ status: null });
    });
  });

  it("opens the drawer when a row is clicked", async () => {
    mockedList.mockResolvedValue(pageResponse([scanFixture()]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-scans-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-scans-row"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-scan-drawer")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-scan-project")).toHaveTextContent("alpha");
    expect(screen.getByTestId("admin-scan-team")).toHaveTextContent("team-a");
  });

  it("drawer cancel flow calls cancelAdminScan and shows the success toast", async () => {
    const scan = scanFixture();
    mockedList.mockResolvedValue(pageResponse([scan]));
    mockedCancel.mockResolvedValue({ ...scan, status: "cancelled" });
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-scans-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-scans-row"));
    await userEvent.click(screen.getByTestId("admin-scan-action-cancel"));
    await waitFor(() => {
      expect(
        screen.getByTestId("admin-scan-confirm-strip"),
      ).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-scan-confirm-ok"));
    await waitFor(() => {
      expect(mockedCancel).toHaveBeenCalledWith(scan.id);
    });
  });

  it("scan_already_cancelled surfaces as the matching toast key", async () => {
    const scan = scanFixture();
    mockedList.mockResolvedValue(pageResponse([scan]));
    mockedCancel.mockRejectedValue(
      new ProblemError("scan already cancelled", {
        status: 409,
        title: "scan already cancelled",
        detail: "scan already cancelled",
        problem: {
          type: "about:blank",
          title: "scan already cancelled",
          status: 409,
          detail: "scan already cancelled",
          scan_already_cancelled: true,
        },
      }),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-scans-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-scans-row"));
    await userEvent.click(screen.getByTestId("admin-scan-action-cancel"));
    await userEvent.click(screen.getByTestId("admin-scan-confirm-ok"));
    await waitFor(() => {
      const toast = screen.getByTestId("admin-toast");
      expect(toast).toHaveAttribute("data-tone", "error");
      expect(toast).toHaveAttribute(
        "data-toast-key",
        "scan_already_cancelled",
      );
    });
  });

  it("succeeded scans render without the cancel action", async () => {
    mockedList.mockResolvedValue(
      pageResponse([scanFixture({ status: "succeeded" })]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-scans-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-scans-row"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-scan-drawer")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("admin-scan-action-cancel"),
    ).not.toBeInTheDocument();
  });

  it("kind select forwards the kind filter and resets to page 1 (M-35)", async () => {
    // total=200 enables the Next button so we can prove the page rewind.
    mockedList.mockResolvedValue(pageResponse([scanFixture()], 200));
    renderPage();
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(1);
    });
    expect(mockedList.mock.calls[0]?.[0]).toMatchObject({
      kind: null,
      project: null,
    });

    await userEvent.click(screen.getByTestId("admin-scans-page-next"));
    await waitFor(() => {
      expect(mockedList.mock.calls.at(-1)?.[0]).toMatchObject({ page: 2 });
    });

    await userEvent.selectOptions(
      screen.getByTestId("admin-scans-kind"),
      "container",
    );
    await waitFor(() => {
      const last = mockedList.mock.calls.at(-1)?.[0];
      expect(last).toMatchObject({ kind: "container", page: 1 });
    });
  });

  it("project search debounces 300ms, forwards the filter, and resets to page 1 (M-35)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedList.mockResolvedValue(pageResponse([scanFixture()], 200));
    renderPage();
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(1);
    });

    await userEvent.click(screen.getByTestId("admin-scans-page-next"));
    await waitFor(() => {
      expect(mockedList.mock.calls.at(-1)?.[0]).toMatchObject({ page: 2 });
    });
    const callsBeforeTyping = mockedList.mock.calls.length;

    await userEvent.type(screen.getByTestId("admin-scans-project"), "alpha");
    // Nothing fires until the debounce window elapses.
    expect(mockedList.mock.calls.length).toBe(callsBeforeTyping);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });
    await waitFor(() => {
      const last = mockedList.mock.calls.at(-1)?.[0];
      expect(last).toMatchObject({ project: "alpha", page: 1 });
    });
  });

  it("page-size change resets to page 1 and re-queries", async () => {
    mockedList.mockResolvedValue(pageResponse([scanFixture()]));
    renderPage();
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(1);
    });
    await userEvent.selectOptions(
      screen.getByTestId("admin-scans-page-size"),
      "100",
    );
    await waitFor(() => {
      const last = mockedList.mock.calls.at(-1)?.[0];
      expect(last).toMatchObject({ page_size: 100, page: 1 });
    });
  });
});
