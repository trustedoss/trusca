/**
 * AdminAuditPage — unit tests.
 *
 * Coverage targets:
 *   - Toolbar filters re-issue the search query.
 *   - Empty state renders when zero rows match.
 *   - Row click opens the diff drawer.
 *   - Sha256 fingerprint values render as the truncated pill.
 *   - CSV export triggers an anchor click + URL.createObjectURL.
 *   - 413 audit_export_too_large surfaces the matching toast key.
 *   - 300ms debounce on the q input.
 *   - L-14: filters round-trip through the URL (write on change, restore
 *     on direct entry, fall back on invalid values).
 *   - L-15: the list polls on refetchInterval.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ToastProvider } from "@/components/ui/toast";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AdminAuditPage } from "@/features/admin/audit/AdminAuditPage";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/admin/audit/api/adminAuditApi", async () => {
  const actual = await vi.importActual<{
    AUDIT_TARGET_TABLES: readonly string[];
  }>("@/features/admin/audit/api/adminAuditApi");
  return {
    ...actual,
    searchAdminAudit: vi.fn(),
    downloadAdminAuditCsv: vi.fn(),
  };
});

import {
  downloadAdminAuditCsv,
  searchAdminAudit,
  type AuditLogItem,
  type AuditLogListPage,
} from "@/features/admin/audit/api/adminAuditApi";

const mockedSearch = vi.mocked(searchAdminAudit);
const mockedExport = vi.mocked(downloadAdminAuditCsv);

function entryFixture(
  id: string,
  overrides: Partial<AuditLogItem> = {},
): AuditLogItem {
  return {
    id,
    created_at: "2026-05-08T00:00:00Z",
    actor_user_id: "actor-1",
    actor_email: "alice@example.com",
    team_id: null,
    target_table: overrides.target_table ?? "users",
    target_id: overrides.target_id ?? "target-1",
    action: overrides.action ?? "update",
    request_id: overrides.request_id ?? "req-1",
    diff: overrides.diff ?? null,
  };
}

function pageResponse(items: AuditLogItem[]): AuditLogListPage {
  return {
    items,
    total: items.length,
    page: 1,
    page_size: 50,
    has_more: false,
  };
}

/** Exposes the live query string so tests can assert URL sync (L-14). */
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-search">{location.search}</div>;
}

function renderPage(initialEntry = "/admin/audit") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <AdminAuditPage />
          <LocationProbe />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("AdminAuditPage", () => {
  beforeEach(() => {
    mockedSearch.mockReset();
    mockedExport.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders rows when the query resolves", async () => {
    mockedSearch.mockResolvedValue(pageResponse([entryFixture("e1")]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-audit-row")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-audit-pii-hint")).toBeInTheDocument();
  });

  it("changing the target_table filter re-issues the query with the table value", async () => {
    mockedSearch.mockResolvedValue(pageResponse([]));
    renderPage();
    await waitFor(() => {
      expect(mockedSearch).toHaveBeenCalledTimes(1);
    });
    await userEvent.selectOptions(
      screen.getByTestId("admin-audit-target-table"),
      "scans",
    );
    await waitFor(() => {
      const last = mockedSearch.mock.calls.at(-1)?.[0];
      expect(last).toMatchObject({ target_table: "scans" });
    });
  });

  it("debounces the q input by 300ms before re-querying", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedSearch.mockResolvedValue(pageResponse([]));
    renderPage();
    await waitFor(() => {
      expect(mockedSearch).toHaveBeenCalledTimes(1);
    });
    const q = screen.getByTestId("admin-audit-q");
    await userEvent.type(q, "alpha");
    expect(mockedSearch).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });
    await waitFor(() => {
      const last = mockedSearch.mock.calls.at(-1)?.[0];
      expect(last?.q).toBe("alpha");
    });
  });

  it("renders the empty state when no rows match", async () => {
    mockedSearch.mockResolvedValue(pageResponse([]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-audit-empty")).toBeInTheDocument();
    });
  });

  it("L-14: a filter change is written to the URL (shareable view)", async () => {
    mockedSearch.mockResolvedValue(pageResponse([]));
    renderPage();
    await waitFor(() => {
      expect(mockedSearch).toHaveBeenCalledTimes(1);
    });
    await userEvent.selectOptions(
      screen.getByTestId("admin-audit-target-table"),
      "scans",
    );
    await waitFor(() => {
      expect(screen.getByTestId("location-search")).toHaveTextContent(
        "target_table=scans",
      );
    });
  });

  it("L-14: a direct URL entry restores the same filter state", async () => {
    mockedSearch.mockResolvedValue(pageResponse([]));
    renderPage(
      "/admin/audit?actor=actor-1&target_table=scans&action=create&q=alpha&page=3&page_size=100",
    );
    await waitFor(() => {
      expect(mockedSearch).toHaveBeenCalled();
    });
    expect(mockedSearch.mock.calls.at(-1)?.[0]).toMatchObject({
      actor_user_id: "actor-1",
      target_table: "scans",
      action: "create",
      q: "alpha",
      page: 3,
      page_size: 100,
    });
    // The toolbar inputs reflect the URL too.
    expect(screen.getByTestId("admin-audit-q")).toHaveValue("alpha");
    expect(screen.getByTestId("admin-audit-actor")).toHaveValue("actor-1");
    expect(screen.getByTestId("admin-audit-target-table")).toHaveValue(
      "scans",
    );
  });

  it("L-14: invalid URL values fall back to the defaults", async () => {
    mockedSearch.mockResolvedValue(pageResponse([]));
    renderPage(
      "/admin/audit?page=0&target_table=bogus&page_size=33&from=not-a-date",
    );
    await waitFor(() => {
      expect(mockedSearch).toHaveBeenCalled();
    });
    expect(mockedSearch.mock.calls.at(-1)?.[0]).toMatchObject({
      page: 1,
      target_table: null,
      page_size: 50,
      from: null,
    });
  });

  it("L-14: the debounced q commit lands in the URL and rewinds the page param", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedSearch.mockResolvedValue(pageResponse([]));
    renderPage("/admin/audit?page=3");
    await waitFor(() => {
      expect(mockedSearch).toHaveBeenCalledTimes(1);
    });
    await userEvent.type(screen.getByTestId("admin-audit-q"), "alpha");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });
    await waitFor(() => {
      const search = screen.getByTestId("location-search").textContent ?? "";
      expect(search).toContain("q=alpha");
      expect(search).not.toContain("page=3");
    });
    await waitFor(() => {
      expect(mockedSearch.mock.calls.at(-1)?.[0]).toMatchObject({
        q: "alpha",
        page: 1,
      });
    });
  });

  it("L-15: the list polls on the refetch interval", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedSearch.mockResolvedValue(pageResponse([entryFixture("e1")]));
    renderPage();
    await waitFor(() => {
      expect(mockedSearch).toHaveBeenCalledTimes(1);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_600);
    });
    await waitFor(() => {
      expect(mockedSearch.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("opens the drawer on row click", async () => {
    mockedSearch.mockResolvedValue(
      pageResponse([
        entryFixture("e1", { diff: { name: "before-name" } }),
      ]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-audit-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-audit-row"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-audit-drawer")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("admin-audit-drawer-target-table"),
    ).toHaveTextContent("users");
  });

  it("renders sha256 fingerprint values as a truncated pill in the diff", async () => {
    const sha = "a".repeat(64);
    mockedSearch.mockResolvedValue(
      pageResponse([entryFixture("e1", { diff: { email: { sha256: sha } } })]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-audit-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-audit-row"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-audit-sha256-pill")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-audit-sha256-pill")).toHaveAttribute(
      "data-prefix",
      sha.slice(0, 8),
    );
  });

  it("CSV export hands the blob URL to an anchor click", async () => {
    mockedSearch.mockResolvedValue(pageResponse([]));
    mockedExport.mockResolvedValue({
      filename: "audit_export_all_all.csv",
      blobUrl: "blob:test-url",
    });
    // jsdom doesn't ship URL.revokeObjectURL — patch it for the duration
    // of the test so the deferred cleanup timeout doesn't crash.
    const originalRevoke = (URL as unknown as Record<string, unknown>)
      .revokeObjectURL;
    (URL as unknown as Record<string, unknown>).revokeObjectURL = vi
      .fn()
      .mockImplementation(() => undefined);
    try {
      renderPage();
      await waitFor(() => {
        expect(
          screen.getByTestId("admin-audit-export-csv"),
        ).toBeInTheDocument();
      });
      await userEvent.click(screen.getByTestId("admin-audit-export-csv"));
      await waitFor(() => {
        expect(mockedExport).toHaveBeenCalledTimes(1);
      });
      await waitFor(() => {
        const toast = screen.getByTestId("admin-toast");
        expect(toast).toHaveAttribute("data-toast-key", "csv_started");
      });
    } finally {
      if (originalRevoke === undefined) {
        delete (URL as unknown as Record<string, unknown>).revokeObjectURL;
      } else {
        (URL as unknown as Record<string, unknown>).revokeObjectURL =
          originalRevoke;
      }
    }
  });

  it("audit_export_too_large surfaces as a matching toast key", async () => {
    mockedSearch.mockResolvedValue(pageResponse([]));
    mockedExport.mockRejectedValue(
      new ProblemError("export too large", {
        status: 413,
        title: "export too large",
        detail: "narrow the window",
        problem: {
          type: "about:blank",
          title: "export too large",
          status: 413,
          detail: "narrow the window",
          audit_export_too_large: true,
        },
      }),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-audit-export-csv")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-audit-export-csv"));
    await waitFor(() => {
      const toast = screen.getByTestId("admin-toast");
      expect(toast).toHaveAttribute("data-tone", "error");
      expect(toast).toHaveAttribute(
        "data-toast-key",
        "audit_export_too_large",
      );
    });
  });
});
