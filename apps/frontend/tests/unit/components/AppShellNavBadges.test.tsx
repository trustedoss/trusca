/**
 * Sidebar count badges (C1).
 *
 * The interesting cases are the ones where the number is NOT known: a cold
 * cache, a failed request, one of the two scan states still in flight. The
 * sidebar has to say nothing in all of them rather than say "0".
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/AppShell";
import { useAuthStore, type AuthUser } from "@/stores/authStore";
import { useUIStore } from "@/stores/uiStore";

/**
 * Stubbed at the axios instance rather than at the two API functions, so the
 * request each badge actually sends - path and query parameters included - is
 * part of what these tests hold still.
 */
const apiGet = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      get: (url: string, config?: { params?: Record<string, unknown> }) =>
        apiGet(url, config?.params ?? {}),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
    },
  };
});

// The bell has its own poll and its own network read; neither is under test.
vi.mock("@/features/notifications/useNotifications", () => ({
  useUnreadCount: () => ({ data: undefined }),
}));

const fakeUser: AuthUser = {
  id: "u-1",
  email: "alice@example.com",
  displayName: "Alice",
  role: "developer",
  isActive: true,
  isSuperuser: false,
  teamId: null,
  teams: [],
};

const ACTION_QUEUE_URL = "/v1/dashboard/action-queue";
const SCANS_URL = "/v1/scans";

/** Records every scan read so a status mix-up cannot hide behind a sum. */
const scanCalls: { status?: string; size?: number }[] = [];

/**
 * Routes by URL, answering the scan reads by their `status` parameter so the
 * order the two land in cannot mask which number came from where.
 *
 * `scans` maps a status to a total, or to a promise that never settles when
 * that read is meant to still be in flight.
 */
function respond(options: {
  approvals?: number | Promise<never>;
  scans?: Record<string, number | Promise<never>>;
}) {
  return (url: string, params: { status?: string; size?: number }) => {
    if (url === ACTION_QUEUE_URL) {
      const approvals = options.approvals;
      if (approvals === undefined) return new Promise(() => {});
      if (typeof approvals !== "number") return approvals;
      return Promise.resolve({ data: { pending_approvals: approvals } });
    }
    if (url === SCANS_URL) {
      scanCalls.push(params);
      const answer = options.scans?.[params.status ?? ""];
      if (answer === undefined) return new Promise(() => {});
      if (typeof answer !== "number") return answer;
      return Promise.resolve({
        data: { items: [], total: answer, page: 1, size: 1 },
      });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  };
}

function renderShell() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects"]}>
        <AppShell />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("sidebar count badges", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useUIStore.setState({ sidebarCollapsed: false });
    useAuthStore.setState({
      user: fakeUser,
      accessToken: "tok",
      status: "authenticated",
      isAuthenticated: true,
    });
    apiGet.mockReset();
    scanCalls.length = 0;
  });
  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it("sums running and queued scans into one badge", async () => {
    apiGet.mockImplementation(
      respond({ approvals: 4, scans: { running: 2, queued: 3 } }),
    );

    renderShell();

    await waitFor(() => {
      expect(screen.getAllByTestId("nav-scans-badge")[0]).toHaveTextContent(
        "5",
      );
    });
    expect(screen.getAllByTestId("nav-approvals-badge")[0]).toHaveTextContent(
      "4",
    );
    // Two reads because GET /v1/scans takes a single status. If it ever
    // accepts a repeated one, this assertion is the reminder to drop a read.
    expect(new Set(scanCalls.map((call) => call.status))).toEqual(
      new Set(["running", "queued"]),
    );
    // The rows are thrown away; asking for a page of 20 to read one integer
    // would pull scan payloads into every screen in the product.
    expect(scanCalls.every((call) => call.size === 1)).toBe(true);
  });

  it("says nothing at all when there is no work waiting", async () => {
    apiGet.mockImplementation(
      respond({ approvals: 0, scans: { running: 0, queued: 0 } }),
    );

    renderShell();

    await waitFor(() => {
      expect(scanCalls).toHaveLength(2);
    });
    expect(screen.queryByTestId("nav-scans-badge")).toBeNull();
    expect(screen.queryByTestId("nav-approvals-badge")).toBeNull();
  });

  it("draws no badge while the counts are still unknown", async () => {
    // A cache that has not answered yet is not the same as a queue that is
    // empty, and the sidebar must not claim the second.
    apiGet.mockImplementation(respond({}));

    renderShell();

    await screen.findAllByTestId("nav-scans");
    expect(screen.queryByTestId("nav-scans-badge")).toBeNull();
    expect(screen.queryByTestId("nav-approvals-badge")).toBeNull();
  });

  it("draws no scan badge when only one of the two states answered", async () => {
    // Summing a resolved 3 with a still-pending state would show 3 while 8
    // scans were actually in flight.
    apiGet.mockImplementation(respond({ approvals: 1, scans: { running: 3 } }));

    renderShell();

    await waitFor(() => {
      expect(screen.getAllByTestId("nav-approvals-badge")[0]).toHaveTextContent(
        "1",
      );
    });
    expect(screen.queryByTestId("nav-scans-badge")).toBeNull();
  });

  it("keeps quiet when the count request fails", async () => {
    apiGet.mockRejectedValue(new Error("boom"));

    renderShell();

    await waitFor(() => {
      expect(apiGet).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("nav-scans-badge")).toBeNull();
    expect(screen.queryByTestId("nav-approvals-badge")).toBeNull();
  });

  it("caps a large count and folds it into the accessible name", async () => {
    apiGet.mockImplementation(
      respond({ approvals: 250, scans: { running: 0, queued: 0 } }),
    );

    renderShell();

    const badge = (await screen.findAllByTestId("nav-approvals-badge"))[0];
    expect(badge).toHaveTextContent("99+");
    // aria-hidden on the pill, count in the link's name: a reader should
    // hear the number once, attached to the destination it belongs to.
    expect(badge).toHaveAttribute("aria-hidden", "true");
    const link = screen.getAllByTestId("nav-approvals")[0];
    // WCAG 2.5.3: the accessible name still starts with the visible label,
    // so "click Approvals" reaches this row.
    expect(link.getAttribute("aria-label")).toBe("Approvals, 250 waiting");
  });

  it("survives the collapsed rail, where the label is gone", async () => {
    // The rail is where a count matters most: with no labels, the badge is
    // the only thing on screen saying anything is waiting.
    useUIStore.setState({ sidebarCollapsed: true });
    apiGet.mockImplementation(
      respond({ approvals: 4, scans: { running: 1, queued: 0 } }),
    );

    renderShell();

    expect(
      (await screen.findAllByTestId("nav-approvals-badge"))[0],
    ).toHaveTextContent("4");
    const link = screen.getAllByTestId("nav-approvals")[0];
    expect(link.getAttribute("aria-label")).toBe("Approvals, 4 waiting");
    // The hover tooltip is the sighted equivalent and has to agree with it.
    expect(link.getAttribute("title")).toBe("Approvals, 4 waiting");
  });

  it("leaves rows without a badge alone", async () => {
    apiGet.mockImplementation(
      respond({ approvals: 4, scans: { running: 2, queued: 3 } }),
    );

    renderShell();

    await screen.findAllByTestId("nav-approvals-badge");
    expect(screen.queryByTestId("nav-projects-badge")).toBeNull();
    // An unbadged row keeps its plain accessible name in the expanded rail.
    expect(screen.getAllByTestId("nav-projects")[0]).not.toHaveAttribute(
      "aria-label",
    );
  });
});
