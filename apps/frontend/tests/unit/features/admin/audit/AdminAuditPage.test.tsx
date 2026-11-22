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

import { ToastProvider } from "@/components/primer/toast";
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

vi.mock("@/features/admin/api/adminUsersApi", async () => {
  const actual = await vi.importActual<object>(
    "@/features/admin/api/adminUsersApi",
  );
  return { ...actual, listAdminUsers: vi.fn(), getAdminUser: vi.fn() };
});

import {
  getAdminUser,
  listAdminUsers,
  type AdminUserDetail,
  type AdminUserListItem,
} from "@/features/admin/api/adminUsersApi";

import {
  AUDIT_ACTIONS,
  downloadAdminAuditCsv,
  searchAdminAudit,
  type AuditLogItem,
  type AuditLogListPage,
} from "@/features/admin/audit/api/adminAuditApi";

const mockedSearch = vi.mocked(searchAdminAudit);
const mockedExport = vi.mocked(downloadAdminAuditCsv);
const mockedListUsers = vi.mocked(listAdminUsers);
const mockedGetUser = vi.mocked(getAdminUser);

const ALICE_ID = "11111111-2222-4333-8444-555555555555";
const BOB_ID = "66666666-7777-4888-8999-000000000000";

function userFixture(
  id: string,
  email: string,
  fullName: string | null,
): AdminUserListItem {
  return {
    id,
    email,
    full_name: fullName,
    is_active: true,
    is_superuser: false,
    last_login_at: null,
    created_at: "2026-05-01T00:00:00Z",
  };
}

function dataVariant(el: HTMLElement): string | null | undefined {
  return (
    el.getAttribute("data-variant") ??
    el.closest("[data-variant]")?.getAttribute("data-variant")
  );
}

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
    mockedListUsers.mockReset();
    mockedGetUser.mockReset();
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
    expect(screen.getByTestId("admin-audit-actor")).toHaveAttribute(
      "data-actor-id",
      "actor-1",
    );
    expect(screen.getByTestId("admin-audit-action")).toHaveValue("create");
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

  it("shows the row and its drawer the same instant, formatted the same way (B3)", async () => {
    // Both surfaces printed the raw UTC ISO the backend sends, beside
    // relative times elsewhere in the product that render in the reader's
    // own zone. Whatever they show now, they must agree.
    mockedSearch.mockResolvedValue(pageResponse([entryFixture("e1")]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-audit-row")).toBeInTheDocument();
    });

    const cell = screen.getByTestId("admin-audit-created-at");
    // Not the wire value, and it says which zone it is in.
    expect(cell.textContent).not.toBe("2026-05-08T00:00:00Z");
    expect(cell.textContent).toMatch(/UTC[+-]/);
    // The machine-readable instant is still there for anyone parsing it.
    expect(cell.getAttribute("dateTime")).toBe("2026-05-08T00:00:00Z");

    await userEvent.click(screen.getByTestId("admin-audit-row"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-audit-drawer")).toBeInTheDocument();
    });

    expect(
      screen.getByTestId("admin-audit-drawer-created-at").textContent,
    ).toBe(cell.textContent);
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

  describe("filter row", () => {
    it("picks the actor by name and filters by that user's id", async () => {
      mockedSearch.mockResolvedValue(pageResponse([]));
      mockedListUsers.mockResolvedValue({
        items: [
          userFixture(ALICE_ID, "alice@example.com", "Alice Kim"),
          userFixture(BOB_ID, "bob@example.com", null),
        ],
        total: 2,
        page: 1,
        page_size: 20,
      });
      mockedGetUser.mockResolvedValue({
        ...userFixture(ALICE_ID, "alice@example.com", "Alice Kim"),
        updated_at: "2026-05-01T00:00:00Z",
        scan_count: 0,
        mfa_enabled: false,
        memberships: [],
      } as AdminUserDetail);
      renderPage();

      // Nothing is listed until the picker opens.
      expect(mockedListUsers).not.toHaveBeenCalled();
      await userEvent.click(screen.getByTestId("admin-audit-actor"));
      await userEvent.click(await screen.findByText("Alice Kim"));

      await waitFor(() => {
        expect(mockedSearch.mock.calls.at(-1)?.[0]).toMatchObject({
          actor_user_id: ALICE_ID,
        });
      });
      expect(screen.getByTestId("location-search")).toHaveTextContent(
        `actor=${ALICE_ID}`,
      );
      // The trigger names the person, not the id.
      await waitFor(() => {
        expect(screen.getByTestId("admin-audit-actor")).toHaveTextContent(
          "alice@example.com",
        );
      });
      expect(mockedGetUser).toHaveBeenCalledWith(ALICE_ID);
    });

    it("sends the typed term to the users search rather than filtering one page", async () => {
      mockedSearch.mockResolvedValue(pageResponse([]));
      mockedListUsers.mockResolvedValue({
        items: [userFixture(ALICE_ID, "alice@example.com", "Alice Kim")],
        total: 1,
        page: 1,
        page_size: 20,
      });
      renderPage();
      await userEvent.click(screen.getByTestId("admin-audit-actor"));
      await screen.findByText("Alice Kim");
      mockedListUsers.mockResolvedValue({
        items: [userFixture(BOB_ID, "bob@example.com", "Bob Lee")],
        total: 1,
        page: 1,
        page_size: 20,
      });
      await userEvent.type(screen.getByTestId("admin-audit-actor-search"), "bob");
      expect(
        await screen.findByText("Bob Lee", {}, { timeout: 2000 }),
      ).toBeInTheDocument();
      expect(mockedListUsers).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: "bob" }),
      );
    });

    it("clears the actor from the picker", async () => {
      mockedSearch.mockResolvedValue(pageResponse([]));
      mockedListUsers.mockResolvedValue({
        items: [],
        total: 0,
        page: 1,
        page_size: 20,
      });
      mockedGetUser.mockRejectedValue(new Error("gone"));
      renderPage(`/admin/audit?actor=${ALICE_ID}`);
      // A deleted account cannot be looked up; the id is shown instead.
      await waitFor(() => {
        expect(screen.getByTestId("admin-audit-actor")).toHaveTextContent(
          ALICE_ID,
        );
      });
      await userEvent.click(screen.getByTestId("admin-audit-actor"));
      await userEvent.click(await screen.findByTestId("admin-audit-actor-clear"));
      await waitFor(() => {
        expect(mockedSearch.mock.calls.at(-1)?.[0]).toMatchObject({
          actor_user_id: null,
        });
      });
      expect(screen.getByTestId("location-search")).not.toHaveTextContent(
        "actor=",
      );
    });

    it("says the user list is cut short, and says so when it fails to load", async () => {
      mockedSearch.mockResolvedValue(pageResponse([]));
      mockedListUsers.mockResolvedValue({
        items: [userFixture(ALICE_ID, "alice@example.com", "Alice Kim")],
        total: 45,
        page: 1,
        page_size: 20,
      });
      renderPage();
      await userEvent.click(screen.getByTestId("admin-audit-actor"));
      expect(
        await screen.findByTestId("admin-audit-actor-truncated"),
      ).toHaveTextContent("first 20");

      mockedListUsers.mockRejectedValue(new Error("boom"));
      await userEvent.type(screen.getByTestId("admin-audit-actor-search"), "zz");
      expect(
        await screen.findByTestId("admin-audit-actor-error", {}, { timeout: 2000 }),
      ).toHaveTextContent("Users could not be loaded.");
    });

    it("offers the known actions as a select and filters by the chosen one", async () => {
      mockedSearch.mockResolvedValue(pageResponse([]));
      renderPage();
      const select = screen.getByTestId("admin-audit-action");
      const values = Array.from(
        select.querySelectorAll("option"),
        (o) => o.getAttribute("value"),
      );
      expect(values).toEqual(["all", ...AUDIT_ACTIONS]);

      await userEvent.selectOptions(select, "backup.restored");
      await waitFor(() => {
        expect(mockedSearch.mock.calls.at(-1)?.[0]).toMatchObject({
          action: "backup.restored",
        });
      });
      await userEvent.selectOptions(select, "all");
      await waitFor(() => {
        expect(mockedSearch.mock.calls.at(-1)?.[0]).toMatchObject({
          action: null,
        });
      });
    });

    it("keeps an action from the URL that the list does not know", async () => {
      mockedSearch.mockResolvedValue(pageResponse([]));
      renderPage("/admin/audit?action=legacy.verb");
      expect(screen.getByTestId("admin-audit-action")).toHaveValue(
        "legacy.verb",
      );
      expect(mockedSearch.mock.calls.at(-1)?.[0]).toMatchObject({
        action: "legacy.verb",
      });
    });

    it("keeps Export CSV secondary on this read-only page", async () => {
      mockedSearch.mockResolvedValue(pageResponse([]));
      renderPage();
      const exportButton = screen.getByTestId("admin-audit-export-csv");
      expect(dataVariant(exportButton)).toBe("default");
      expect(exportButton.closest("header")).not.toBeNull();
    });
  });

  it("replaces the table with an empty state when nothing matches", async () => {
    mockedSearch.mockResolvedValue(pageResponse([]));
    renderPage();
    const empty = await screen.findByTestId("admin-audit-empty");
    expect(empty).toHaveAttribute("role", "status");
    expect(empty).toHaveTextContent("No audit entries found");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByTestId("admin-audit-table")).toHaveAttribute(
      "aria-busy",
      "false",
    );
  });
});
