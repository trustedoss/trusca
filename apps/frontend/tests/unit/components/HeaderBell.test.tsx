/**
 * HeaderBell — unit tests for chore A2.
 *
 * Coverage:
 *   - formatBadge() collapses 0 / 3 / 99 / 100 / 250 to the right strings.
 *   - Rendered badge mirrors the count (hidden at 0, "99+" once > 99).
 *   - Click navigates to /notifications.
 *   - Polling: refetchInterval is set to 60s and refetchIntervalInBackground
 *     is false, so the query is paused while the tab is hidden — we assert
 *     by mocking `getUnreadCount` and toggling document.visibilityState.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatBadge, HeaderBell } from "@/components/HeaderBell";

vi.mock("@/features/notifications/api/notificationsApi", () => ({
  listNotifications: vi.fn(),
  markRead: vi.fn(),
  markAllRead: vi.fn(),
  getUnreadCount: vi.fn(),
}));

import { getUnreadCount } from "@/features/notifications/api/notificationsApi";

const mockedCount = vi.mocked(getUnreadCount);

function renderBell(initialEntry = "/projects") {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route
            path="/projects"
            element={
              <div>
                <HeaderBell />
                <span data-testid="route-marker">projects</span>
              </div>
            }
          />
          <Route
            path="/notifications"
            element={<span data-testid="route-marker">notifications</span>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("formatBadge", () => {
  it("returns empty string for 0", () => {
    expect(formatBadge(0)).toBe("");
  });

  it("returns empty string for negative counts (defensive)", () => {
    expect(formatBadge(-5)).toBe("");
  });

  it("formats a single-digit count as itself", () => {
    expect(formatBadge(3)).toBe("3");
  });

  it("formats 99 as '99'", () => {
    expect(formatBadge(99)).toBe("99");
  });

  it("caps anything over 99 at '99+'", () => {
    expect(formatBadge(100)).toBe("99+");
    expect(formatBadge(250)).toBe("99+");
  });
});

describe("HeaderBell", () => {
  beforeEach(() => {
    mockedCount.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("hides the badge while the count is 0", async () => {
    mockedCount.mockResolvedValueOnce({ count: 0 });
    renderBell();

    await waitFor(() => {
      expect(mockedCount).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByTestId("header-bell-badge")).not.toBeInTheDocument();
    // The button itself is always rendered.
    expect(screen.getByTestId("header-bell")).toBeInTheDocument();
  });

  it("shows a numeric badge for counts in [1, 99]", async () => {
    mockedCount.mockResolvedValueOnce({ count: 7 });
    renderBell();

    await waitFor(() => {
      expect(screen.getByTestId("header-bell-badge")).toHaveTextContent("7");
    });
    expect(screen.getByTestId("header-bell")).toHaveAttribute(
      "data-unread-count",
      "7",
    );
  });

  it("collapses any count > 99 to '99+'", async () => {
    mockedCount.mockResolvedValueOnce({ count: 257 });
    renderBell();

    await waitFor(() => {
      expect(screen.getByTestId("header-bell-badge")).toHaveTextContent("99+");
    });
  });

  it("navigates to /notifications when the bell is clicked", async () => {
    mockedCount.mockResolvedValue({ count: 0 });
    renderBell();

    await waitFor(() => {
      expect(screen.getByTestId("header-bell")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("header-bell"));

    await waitFor(() => {
      expect(screen.getByTestId("route-marker")).toHaveTextContent(
        "notifications",
      );
    });
  });

  it("does not poll while the tab is hidden", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedCount.mockResolvedValue({ count: 1 });

    // Mark the tab hidden BEFORE the component mounts so the initial
    // refetch interval starts in the paused state. TanStack Query's
    // `focusManager` listens on `visibilitychange`; toggling the
    // descriptor reflects the same surface the production hook reads.
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });

    renderBell();

    // Initial fetch resolves once; from there the timer is paused.
    await waitFor(() => {
      expect(mockedCount).toHaveBeenCalledTimes(1);
    });

    // Advance well past the 60s interval — no extra refetch should fire.
    await vi.advanceTimersByTimeAsync(180_000);
    expect(mockedCount).toHaveBeenCalledTimes(1);

    // Restore for subsequent tests.
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
  });
});
