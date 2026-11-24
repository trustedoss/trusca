/**
 * NotificationsPage — unit tests for chore A2.
 *
 * Coverage targets (chore A2 spec):
 *   1. Renders rows when the list query resolves.
 *   2. Toggling "unread only" re-issues the list with unread_only=true.
 *   3. Clicking a row marks it read AND navigates to its `link`.
 *   4. "Mark all as read" only renders when unread_count > 0; click
 *      invokes the mutation.
 *   5. A channel switch saves on change (PUT echo); a failed save puts
 *      the switch back and shows an inline error.
 *   6. The in-app switch is rendered checked AND disabled.
 *   7. Rows render title/body from the catalog by `message_key` in EN and
 *      KO, and fall back to the stored text for an unknown or absent key.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationsPage } from "@/features/notifications/NotificationsPage";
import { formatAbsoluteTime } from "@/lib/absoluteTime";
import i18n from "@/lib/i18n";
import { ProblemError } from "@/lib/problem";
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
    message_key: overrides.message_key ?? null,
    params: overrides.params ?? null,
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

function renderPage(initialEntry = "/notifications") {
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

  // M-19 — the relative timestamp must expose the absolute datetime on hover.
  it("exposes the absolute datetime as the timestamp tooltip (M-19)", async () => {
    mockedList.mockResolvedValueOnce(
      makeList([makeItem("n1", { created_at: "2026-05-08T00:00:00Z" })]),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("notifications-row-time")).toBeInTheDocument();
    });
    // B3: the tooltip now comes from the shared absolute formatter and names
    // the zone it rendered in, so this and the audit log agree.
    expect(screen.getByTestId("notifications-row-time")).toHaveAttribute(
      "title",
      formatAbsoluteTime("2026-05-08T00:00:00Z", "en"),
    );
    expect(
      screen.getByTestId("notifications-row-time").getAttribute("title"),
    ).toMatch(/UTC[+-]/);
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

  it("opens on the filter and page the URL asked for (B1)", async () => {
    // Both were component state, so a reload put the reader on page 1 of
    // everything, whichever page of unread they had been reading.
    // A total that spans more than one page, or the clamp would rightly
    // snap page 2 back to 1 and mask what this test is about.
    mockedList.mockResolvedValue(makeList([], { total: 120 }));

    renderPage("/notifications?unread=1&page=2");

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    expect(mockedList).toHaveBeenLastCalledWith(
      expect.objectContaining({ unread_only: true, page: 2 }),
    );
    expect(screen.getByTestId("notifications-unread-only")).toBeChecked();
  });

  it("snaps a page the inbox does not have back into range (B1)", async () => {
    // A bookmark can name page 2 of an inbox that has since been read down
    // to one page. Without this the footer reads "Page 2 of 1".
    mockedList.mockResolvedValue(makeList([]));

    renderPage("/notifications?page=2");

    await waitFor(() => {
      expect(mockedList).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 1 }),
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

  it("saves a channel switch on change, with no Save button", async () => {
    mockedList.mockResolvedValue(makeList([]));

    const initial = defaultPrefs();
    const next: NotificationPrefs = { ...initial, slack_enabled: true };
    mockedGetPrefs.mockReset();
    mockedGetPrefs.mockResolvedValue(initial);
    mockedUpdatePrefs.mockResolvedValueOnce(next);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("notifications-prefs-form")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("notifications-prefs-save")).toBeNull();
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();

    await userEvent.click(screen.getByTestId("notifications-prefs-slack"));

    await waitFor(() => {
      expect(mockedUpdatePrefs).toHaveBeenCalledTimes(1);
    });
    expect(mockedUpdatePrefs).toHaveBeenCalledWith({
      email_enabled: true,
      slack_enabled: true,
      teams_enabled: false,
      in_app_enabled: true,
    });
    await waitFor(() => {
      expect(screen.getByTestId("notifications-prefs-slack")).toBeChecked();
    });
    expect(screen.getByTestId("notifications-prefs-status")).toHaveAttribute(
      "data-saving",
      "false",
    );
    expect(screen.queryByTestId("notifications-prefs-save-error")).toBeNull();
  });

  it("puts the switch back and shows an inline error when the save fails", async () => {
    mockedList.mockResolvedValue(makeList([]));
    mockedGetPrefs.mockReset();
    mockedGetPrefs.mockResolvedValue(defaultPrefs());
    mockedUpdatePrefs.mockRejectedValueOnce(
      new ProblemError("save failed", {
        status: 500,
        title: "Internal Server Error",
        detail: "boom",
        problem: {
          type: "about:blank",
          title: "Internal Server Error",
          status: 500,
          detail: "boom",
        },
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("notifications-prefs-form")).toBeInTheDocument();
    });
    const email = screen.getByTestId("notifications-prefs-email");
    expect(email).toBeChecked();

    await userEvent.click(email);

    await waitFor(() => {
      expect(
        screen.getByTestId("notifications-prefs-save-error"),
      ).toBeInTheDocument();
    });
    // Reverted to what the server still holds.
    expect(screen.getByTestId("notifications-prefs-email")).toBeChecked();
    expect(screen.getByTestId("notifications-prefs-email")).not.toBeDisabled();
    expect(mockedUpdatePrefs).toHaveBeenCalledWith(
      expect.objectContaining({ email_enabled: false }),
    );
  });

  it("sends in-app as on even when the server row says off", async () => {
    // The server rejects in_app_enabled=false; a stale row must not turn
    // every toggle into a failed save.
    mockedList.mockResolvedValue(makeList([]));
    mockedGetPrefs.mockReset();
    mockedGetPrefs.mockResolvedValue({ ...defaultPrefs(), in_app_enabled: false });
    mockedUpdatePrefs.mockResolvedValueOnce({
      ...defaultPrefs(),
      teams_enabled: true,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("notifications-prefs-teams")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("notifications-prefs-teams"));

    await waitFor(() => {
      expect(mockedUpdatePrefs).toHaveBeenCalledWith(
        expect.objectContaining({ teams_enabled: true, in_app_enabled: true }),
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

  describe("message rendering by message_key", () => {
    afterEach(async () => {
      await i18n.changeLanguage("en");
    });

    const keyed = [
      makeItem("k1", {
        kind: "scan_completed",
        title: "Scheduled scan completed: web",
        body: "The scheduled scan finished. Review the results when you have a moment.",
        message_key: "scan_scheduled_completed",
        params: { project_name: "web" },
      }),
      makeItem("k2", {
        kind: "vuln_sla_breach",
        title: "SLA breach: 3 findings overdue in api",
        body: "stored english",
        message_key: "vuln_sla_breach",
        params: {
          project_name: "api",
          count: 3,
          since: "2026-07-24T02:45:00+00:00",
          critical: 1,
          high: 2,
        },
      }),
      makeItem("k3", {
        kind: "account_security",
        title: "Your second factor was removed",
        body: "stored english",
        message_key: "mfa_cleared_by_admin",
      }),
    ];

    function rowText(id: string): string {
      const row = screen
        .getAllByTestId("notifications-row")
        .find((el) => el.getAttribute("data-notification-id") === id);
      return row?.textContent ?? "";
    }

    it("renders English from the catalog", async () => {
      mockedList.mockResolvedValue(makeList(keyed));
      renderPage();
      await waitFor(() => {
        expect(screen.getAllByTestId("notifications-row")).toHaveLength(3);
      });
      expect(rowText("k1")).toContain("Scheduled scan completed: web");
      expect(rowText("k2")).toContain("SLA breach in api: 3 overdue");
      expect(rowText("k2")).toContain("(Critical 1, High 2)");
      expect(rowText("k2")).not.toContain("stored english");
      expect(rowText("k3")).toContain(
        "An administrator cleared the authenticator app",
      );
    });

    it("renders Korean from the catalog", async () => {
      await i18n.changeLanguage("ko");
      mockedList.mockResolvedValue(makeList(keyed));
      renderPage();
      await waitFor(() => {
        expect(screen.getAllByTestId("notifications-row")).toHaveLength(3);
      });
      expect(rowText("k1")).toContain("예약 스캔 완료: web");
      expect(rowText("k1")).not.toContain("Scheduled scan");
      expect(rowText("k2")).toContain("조치 기한 초과: api, 3건");
      expect(rowText("k2")).toContain("(치명 1, 높음 2)");
      expect(rowText("k3")).toContain("2단계 인증이 해제되었습니다");
      expect(rowText("k3")).not.toContain("stored english");
    });

    it("renders the seeded kinds in both languages without the kind label", async () => {
      const seeded = [
        makeItem("s1", {
          kind: "cve_detected",
          title: "stored english",
          body: "stored english",
          message_key: "cve_detected",
          params: { component: "lodash@1.0.0", cve_id: "CVE-2024-99001" },
        }),
        makeItem("s2", {
          kind: "policy_gate_failed",
          title: "stored english",
          body: "stored english",
          message_key: "policy_gate_failed",
          params: { project_name: "web" },
        }),
      ];
      mockedList.mockResolvedValue(makeList(seeded));
      renderPage();
      await waitFor(() => {
        expect(screen.getAllByTestId("notifications-row")).toHaveLength(2);
      });
      expect(rowText("s1")).toContain("New CVE found in lodash@1.0.0");
      expect(rowText("s1")).toContain("CVE-2024-99001 affects");
      expect(rowText("s2")).toContain("Build gate blocked a build");
      // The kind label repeated the title, so it is no longer printed.
      expect(screen.queryByTestId("notifications-row-kind")).toBeNull();
      await i18n.changeLanguage("ko");
      await waitFor(() => {
        expect(rowText("s1")).toContain("lodash@1.0.0에서 새 CVE 발견");
      });
      expect(rowText("s2")).toContain("빌드 게이트가 빌드를 차단함");
    });

    it("ignores params named after i18next options", async () => {
      // A param called `lng` would otherwise switch the row to Korean.
      mockedList.mockResolvedValue(
        makeList([
          makeItem("r1", {
            kind: "scan_completed",
            title: "stored english",
            body: "stored english",
            message_key: "scan_scheduled_completed",
            params: { project_name: "web", lng: "ko" },
          }),
        ]),
      );
      renderPage();
      await waitFor(() => {
        expect(screen.getAllByTestId("notifications-row")).toHaveLength(1);
      });
      expect(rowText("r1")).toContain("Scheduled scan completed: web");
      expect(rowText("r1")).not.toContain("예약 스캔");
    });

    it("falls back to the stored text for an absent or unknown key", async () => {
      await i18n.changeLanguage("ko");
      mockedList.mockResolvedValue(
        makeList([
          makeItem("old", {
            title: "Scan Completed #1",
            body: "Project scan completed successfully.",
          }),
          makeItem("future", {
            title: "Stored future title",
            body: "Stored future body",
            message_key: "a_message_this_build_does_not_know",
            params: { project_name: "web" },
          }),
          makeItem("dotted", {
            title: "Stored dotted title",
            body: "Stored dotted body",
            // Would walk into another part of the catalog if it were looked up.
            message_key: "kind.scan_completed",
          }),
        ]),
      );
      renderPage();
      await waitFor(() => {
        expect(screen.getAllByTestId("notifications-row")).toHaveLength(3);
      });
      expect(rowText("old")).toContain("Scan Completed #1");
      expect(rowText("old")).toContain("Project scan completed successfully.");
      expect(rowText("future")).toContain("Stored future title");
      expect(rowText("future")).toContain("Stored future body");
      expect(rowText("dotted")).toContain("Stored dotted title");
    });

    it("falls back to the stored text when a param the message needs is missing", async () => {
      mockedList.mockResolvedValue(
        makeList([
          makeItem("thin", {
            title: "Stored title",
            body: "Stored body",
            message_key: "scan_scheduled_completed",
            params: {},
          }),
        ]),
      );
      renderPage();
      await waitFor(() => {
        expect(screen.getByTestId("notifications-row")).toBeInTheDocument();
      });
      expect(rowText("thin")).toContain("Stored title");
      expect(rowText("thin")).not.toContain("{{");
    });
  });
});
