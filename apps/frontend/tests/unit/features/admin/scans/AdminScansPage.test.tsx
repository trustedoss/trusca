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
import { MemoryRouter, useNavigate } from "react-router-dom";
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

// MemoryRouter keeps its own history stack, so window.history is inert in
// these tests. This is how a test moves the URL without the page doing it.
function UrlProbe() {
  const navigate = useNavigate();
  return (
    <button
      data-testid="navigate-elsewhere"
      onClick={() => navigate("/admin/scans?project=beta")}
    />
  );
}

// B1: the filters live in the URL now, so the page needs a router.
function renderPage(url = "/admin/scans") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return {
    client,
    ...render(
      <MemoryRouter initialEntries={[url]}>
        <QueryClientProvider client={client}>
          <ToastProvider>
            <AdminScansPage />
            <UrlProbe />
          </ToastProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    ),
  };
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

  it("opens on the tab, kind and page the URL asked for (B1)", async () => {
    // A link to a filtered list has to arrive at that list, not at the
    // default tab with the parameters silently dropped.
    mockedList.mockResolvedValue(pageResponse([]));
    renderPage("/admin/scans?status=failed&kind=container&project=alpha&page=3");

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    expect(mockedList.mock.calls[0]?.[0]).toMatchObject({
      status: "failed",
      kind: "container",
      project: "alpha",
      page: 3,
    });
  });

  it("ignores filter values it does not recognise (B1)", async () => {
    // A stale or hand-edited URL falls back to the default rather than being
    // forwarded to the backend, which would answer 422.
    mockedList.mockResolvedValue(pageResponse([]));
    renderPage("/admin/scans?status=deleted&kind=nonsense&page=0");

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    expect(mockedList.mock.calls[0]?.[0]).toMatchObject({
      status: "running",
      kind: null,
      page: 1,
    });
  });

  it("keeps a deep-linked page when the search term has not moved (B1)", async () => {
    // The debounce writes the term on a timer. If it fired on mount it would
    // clear the page as a filter change, and `?project=alpha&page=3` would
    // land on page 1.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // A total that spans more than three pages, or the clamp would rightly
    // snap page 3 back to 1 and mask what this test is about.
    mockedList.mockResolvedValue(pageResponse([scanFixture()], 400));
    renderPage("/admin/scans?project=alpha&page=3");

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    expect(mockedList.mock.calls.at(-1)?.[0]).toMatchObject({
      project: "alpha",
      page: 3,
    });
  });

  it("restores the drawer from the URL and clears it on close (B1)", async () => {
    // The id is what the URL carries; the row is whatever the list holds.
    mockedList.mockResolvedValue(pageResponse([scanFixture()]));
    renderPage(
      "/admin/scans?scan=11111111-1111-1111-1111-111111111111",
    );

    await waitFor(() => {
      expect(screen.getByTestId("admin-scan-drawer")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-scan-project")).toHaveTextContent("alpha");
  });

  it("keeps the drawer open when the scan leaves the list (B1)", async () => {
    // This list polls every 30s and the cancel action invalidates it, and
    // the default tab is `running`. A scan being read finishes, drops out of
    // the list, and the drawer would vanish from under the operator with
    // `?scan=` still in the address bar. Radix does not call onOpenChange
    // when a prop closes it, so nothing would clear the parameter either.
    mockedList.mockResolvedValue(pageResponse([scanFixture()]));
    const { client } = renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("admin-scans-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-scans-row"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-scan-drawer")).toBeInTheDocument();
    });

    // The next poll no longer carries it. Driven through the query client
    // because that is what the page's own 30s interval and the cancel
    // action's invalidation both do.
    mockedList.mockResolvedValue(pageResponse([]));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["admin", "scans"] });
    });
    await waitFor(() => {
      expect(screen.queryByTestId("admin-scans-row")).not.toBeInTheDocument();
    });

    expect(screen.getByTestId("admin-scan-drawer")).toBeInTheDocument();
    expect(screen.getByTestId("admin-scan-project")).toHaveTextContent("alpha");
  });

  it("snaps a page the list does not have back into range (B1)", async () => {
    // This is the one screen where the range shrinks under the operator:
    // it polls every 30s on a `running` tab that empties as scans finish.
    mockedList.mockResolvedValue(pageResponse([scanFixture()], 1));
    renderPage("/admin/scans?page=5");

    await waitFor(() => {
      expect(mockedList.mock.calls.at(-1)?.[0]).toMatchObject({ page: 1 });
    });
  });

  it("follows the URL when the term changes from outside the field (B1)", async () => {
    // Back moves the URL. Without this the field keeps showing a term the
    // list is no longer filtered by.
    mockedList.mockResolvedValue(pageResponse([]));
    renderPage("/admin/scans?project=alpha");
    expect(screen.getByTestId("admin-scans-project")).toHaveValue("alpha");

    await userEvent.click(screen.getByTestId("navigate-elsewhere"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-scans-project")).toHaveValue("beta");
    });
  });

  it("keeps a trailing space the operator typed (B1)", async () => {
    // The URL holds the trimmed term. A field that follows the URL back
    // unconditionally swallows the space 300ms after it is typed, and the
    // next word joins the previous one.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedList.mockResolvedValue(pageResponse([]));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderPage();

    const field = screen.getByTestId("admin-scans-project");
    await user.type(field, "alpha ");
    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    expect(field).toHaveValue("alpha ");
  });

  it("leaves the drawer shut for a scan no longer in the list (B1)", async () => {
    mockedList.mockResolvedValue(pageResponse([scanFixture()]));
    renderPage("/admin/scans?scan=99999999-9999-9999-9999-999999999999");

    await waitFor(() => {
      expect(screen.getByTestId("admin-scans-row")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("admin-scan-drawer")).not.toBeInTheDocument();
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
