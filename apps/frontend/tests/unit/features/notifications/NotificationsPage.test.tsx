/**
 * NotificationsPage — unit tests for chore A2.
 *
 * Coverage targets (chore A2 spec):
 *   1. Renders rows when the list query resolves.
 *   2. Toggling "unread only" re-issues the list with unread_only=true.
 *   3. Clicking a row marks it read AND navigates to its `link`.
 *   4. "Mark all as read" only renders when unread_count > 0; click
 *      invokes the mutation.
 *   5. Saving prefs round-trips through `updatePrefs` (PUT echo).
 *   6. The in-app switch is rendered checked AND disabled.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationsPage } from "@/features/notifications/NotificationsPage";
import type {
  NotificationItem,
  NotificationKind,
  NotificationListResponse,
} from "@/features/notifications/api/notificationsApi";
import type { NotificationPrefs } from "@/features/notifications/api/notificationPrefsApi";

vi.mock("@/features/notifications/api/notificationsApi", () => ({
  listNotifications: vi.fn(),
  markRead: vi.fn(),
  markAllRead: vi.fn(),
  getUnreadCount: vi.fn(),
}));

vi.mock("@/features/notifications/api/notificationPrefsApi", () => ({
  getPrefs: vi.fn(),
  updatePrefs: vi.fn(),
}));

import {
  listNotifications,
  markAllRead,
  markRead,
} from "@/features/notifications/api/notificationsApi";
import {
  getPrefs,
  updatePrefs,
} from "@/features/notifications/api/notificationPrefsApi";

const mockedList = vi.mocked(listNotifications);
const mockedMarkRead = vi.mocked(markRead);
const mockedMarkAll = vi.mocked(markAllRead);
const mockedGetPrefs = vi.mocked(getPrefs);
const mockedUpdatePrefs = vi.mocked(updatePrefs);

function makeItem(
  id: string,
  overrides: Partial<NotificationItem> = {},
): NotificationItem {
  return {
    id,
    kind: (overrides.kind ?? "scan_completed") as NotificationKind,
    title: overrides.title ?? `Notification ${id}`,
    body: overrides.body ?? "Body text",
    link: overrides.link ?? null,
    target_table: overrides.target_table ?? null,
    target_id: overrides.target_id ?? null,
    read_at: overrides.read_at ?? null,
    created_at: overrides.created_at ?? "2026-05-08T00:00:00Z",
  };
}

function makeList(
  items: NotificationItem[],
  overrides: Partial<NotificationListResponse> = {},
): NotificationListResponse {
  return {
    items,
    total: overrides.total ?? items.length,
    unread_count:
      overrides.unread_count ??
      items.filter((it) => it.read_at === null).length,
    page: overrides.page ?? 1,
    page_size: overrides.page_size ?? 20,
  };
}

function defaultPrefs(): NotificationPrefs {
  return {
    email_enabled: true,
    slack_enabled: false,
    teams_enabled: false,
    in_app_enabled: true,
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/notifications"]}>
        <Routes>
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route
            path="/scans/:id"
            element={<span data-testid="route-marker">scans-detail</span>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("NotificationsPage", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedMarkRead.mockReset();
    mockedMarkAll.mockReset();
    mockedGetPrefs.mockReset();
    mockedUpdatePrefs.mockReset();
    mockedGetPrefs.mockResolvedValue(defaultPrefs());
  });

  it("renders the list rows once the query resolves", async () => {
    mockedList.mockResolvedValueOnce(
      makeList([
        makeItem("n1", { title: "Scan finished", kind: "scan_completed" }),
        makeItem("n2", { title: "New CVE", kind: "cve_detected" }),
      ]),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getAllByTestId("notifications-row")).toHaveLength(2);
    });
    expect(screen.getByText("Scan finished")).toBeInTheDocument();
    expect(screen.getByText("New CVE")).toBeInTheDocument();
  });

  it("toggling 'unread only' re-issues the list with unread_only=true", async () => {
    mockedList.mockResolvedValue(
      makeList([makeItem("n1", { read_at: null })]),
    );

    renderPage();

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    // Initial call uses unread_only=false.
    expect(mockedList).toHaveBeenLastCalledWith(
      expect.objectContaining({ unread_only: false }),
    );

    await userEvent.click(screen.getByTestId("notifications-unread-only"));

    await waitFor(() => {
      expect(mockedList).toHaveBeenLastCalledWith(
        expect.objectContaining({ unread_only: true, page: 1 }),
      );
    });
  });

  it("clicking a row marks it read and navigates to its link", async () => {
    const item = makeItem("n1", {
      read_at: null,
      link: "/scans/scan-42",
      title: "Open me",
    });
    mockedList.mockResolvedValueOnce(makeList([item]));
    mockedMarkRead.mockResolvedValueOnce(undefined);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("notifications-row")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("notifications-row"));

    await waitFor(() => {
      expect(mockedMarkRead).toHaveBeenCalledWith("n1");
    });
    await waitFor(() => {
      expect(screen.getByTestId("route-marker")).toHaveTextContent(
        "scans-detail",
      );
    });
  });

  it("does NOT call markRead for an already-read row but still navigates", async () => {
    const item = makeItem("n2", {
      read_at: "2026-05-08T01:00:00Z",
      link: "/scans/scan-99",
    });
    mockedList.mockResolvedValueOnce(makeList([item]));

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("notifications-row")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("notifications-row"));

    await waitFor(() => {
      expect(screen.getByTestId("route-marker")).toHaveTextContent(
        "scans-detail",
      );
    });
    expect(mockedMarkRead).not.toHaveBeenCalled();
  });

  it("'Mark all as read' is hidden when unread_count is 0 and shown otherwise", async () => {
    // First render: zero unread → button absent.
    mockedList.mockResolvedValueOnce(
      makeList(
        [makeItem("n1", { read_at: "2026-05-08T01:00:00Z" })],
        { unread_count: 0 },
      ),
    );
    const { unmount } = renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("notifications-list")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("notifications-mark-all")).not.toBeInTheDocument();
    unmount();

    // Second render: with unread → button visible AND clickable.
    // `mockResolvedValue` (not `Once`) so the post-invalidation refetch
    // after the markAllRead mutation also resolves cleanly.
    mockedList.mockResolvedValue(
      makeList([makeItem("n2", { read_at: null })], { unread_count: 1 }),
    );
    mockedMarkAll.mockResolvedValueOnce(undefined);
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("notifications-mark-all")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("notifications-mark-all"));

    await waitFor(() => {
      expect(mockedMarkAll).toHaveBeenCalledTimes(1);
    });
  });

  it("saves preferences via PUT and reflects the echoed row", async () => {
    mockedList.mockResolvedValue(makeList([]));

    const initial = defaultPrefs();
    const next: NotificationPrefs = { ...initial, slack_enabled: true };
    mockedGetPrefs.mockReset();
    mockedGetPrefs.mockResolvedValue(initial);
    mockedUpdatePrefs.mockResolvedValueOnce(next);

    renderPage();

    // Wait for the form to render (not the loading skeleton).
    await waitFor(() => {
      expect(screen.getByTestId("notifications-prefs-form")).toBeInTheDocument();
    });

    // Save is disabled while pristine.
    expect(screen.getByTestId("notifications-prefs-save")).toBeDisabled();

    await userEvent.click(screen.getByTestId("notifications-prefs-slack"));

    // Now dirty → enabled.
    await waitFor(() => {
      expect(screen.getByTestId("notifications-prefs-save")).not.toBeDisabled();
    });

    await userEvent.click(screen.getByTestId("notifications-prefs-save"));

    await waitFor(() => {
      expect(mockedUpdatePrefs).toHaveBeenCalledWith(
        expect.objectContaining({
          email_enabled: true,
          slack_enabled: true,
          teams_enabled: false,
          in_app_enabled: true,
        }),
      );
    });
  });

  it("renders the in-app switch as checked and disabled", async () => {
    mockedList.mockResolvedValue(makeList([]));
    mockedGetPrefs.mockReset();
    // Even if the backend reports in_app_enabled: false, we never give the
    // user a way to flip it from this surface — UI shows it as on + locked.
    mockedGetPrefs.mockResolvedValue({ ...defaultPrefs(), in_app_enabled: false });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("notifications-prefs-in-app")).toBeInTheDocument();
    });
    const inApp = screen.getByTestId("notifications-prefs-in-app");
    expect(inApp).toBeDisabled();
    // The visible switch is forced checked regardless of the wire value.
    expect(inApp).toBeChecked();
  });

  it("renders the empty state when the list is empty", async () => {
    mockedList.mockResolvedValueOnce(makeList([]));
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("notifications-empty")).toBeInTheDocument();
    });
  });
});
