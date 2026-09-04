import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  AnonymisationBacklogPanel,
  OVERDUE_DAYS,
  panelStatus,
} from "@/features/admin/health/AnonymisationBacklogPanel";
import { useAdminAnonymisation } from "@/features/admin/health/api/useAdminAnonymisation";

vi.mock("@/features/admin/health/api/useAdminAnonymisation", () => ({
  useAdminAnonymisation: vi.fn(),
}));

const mocked = vi.mocked(useAdminAnonymisation);

function item(waiting_days: number, id = "11111111-1111-1111-1111-111111111111") {
  return {
    request_id: `req-${waiting_days}`,
    subject_user_id: id,
    requested_by_user_id: "aaaaaaaa-0000-0000-0000-000000000001",
    approved_by_user_id: "bbbbbbbb-0000-0000-0000-000000000002",
    approved_at: "2026-09-01T00:00:00Z",
    waiting_days,
  };
}

function resolved(items: ReturnType<typeof item>[]) {
  mocked.mockReturnValue({
    data: { items, count: items.length },
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useAdminAnonymisation>);
}

describe("panelStatus", () => {
  it("is clear only when nothing is outstanding", () => {
    expect(panelStatus([])).toBe("clear");
  });

  it("escalates on the oldest row, not the newest", () => {
    // The list arrives oldest first, but a status derived from items[0] alone
    // would go wrong the moment that order changed. Newest first here on
    // purpose.
    expect(panelStatus([item(1), item(OVERDUE_DAYS)])).toBe("overdue");
  });

  it("treats the boundary day as overdue", () => {
    expect(panelStatus([item(OVERDUE_DAYS - 1)])).toBe("waiting");
    expect(panelStatus([item(OVERDUE_DAYS)])).toBe("overdue");
  });
});

describe("AnonymisationBacklogPanel", () => {
  it("says nothing is outstanding when the backlog is empty", () => {
    resolved([]);
    render(<AnonymisationBacklogPanel />);
    const panel = screen.getByTestId("anonymisation-backlog-panel");
    expect(panel).toHaveAttribute("data-status", "clear");
    expect(screen.getByTestId("anonymisation-backlog-empty")).toBeInTheDocument();
    expect(screen.queryAllByTestId("anonymisation-backlog-row")).toHaveLength(0);
  });

  it("renders one row per outstanding erasure with its raw waiting days", () => {
    resolved([item(2), item(9)]);
    render(<AnonymisationBacklogPanel />);

    const rows = screen.getAllByTestId("anonymisation-backlog-row");
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.getAttribute("data-waiting-days"))).toEqual(["2", "9"]);
    expect(screen.getByTestId("anonymisation-backlog-panel")).toHaveAttribute(
      "data-status",
      "overdue",
    );
  });

  it("names both parties so the operator has someone to ask", () => {
    // The row is the only thing standing behind an irreversible command, and
    // anything that can write to that table can produce one. Who asked and
    // who agreed is what an operator can actually verify.
    resolved([item(1)]);
    render(<AnonymisationBacklogPanel />);
    const panel = screen.getByTestId("anonymisation-backlog-panel");
    expect(panel.textContent).toContain("aaaaaaaa-0000-0000-0000-000000000001");
    expect(panel.textContent).toContain("bbbbbbbb-0000-0000-0000-000000000002");
  });

  it("shows the subject by id and never by address", () => {
    // The screen that tracks an address's removal must not be the screen that
    // puts it back on a monitor, into a screenshot, and into a support ticket.
    resolved([item(1, "22222222-2222-2222-2222-222222222222")]);
    render(<AnonymisationBacklogPanel />);

    const panel = screen.getByTestId("anonymisation-backlog-panel");
    expect(panel.textContent).toContain("22222222-2222-2222-2222-222222222222");
    expect(panel.textContent).not.toMatch(/@/);
  });

  it("reports a failure instead of rendering an empty backlog", () => {
    // The dangerous failure is a fetch error that looks like "nothing owed".
    mocked.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("boom"),
    } as unknown as ReturnType<typeof useAdminAnonymisation>);

    render(<AnonymisationBacklogPanel />);
    const panel = screen.getByTestId("anonymisation-backlog-panel");
    expect(panel).toHaveAttribute("data-status", "error");
    expect(screen.queryByTestId("anonymisation-backlog-empty")).toBeNull();
  });
});
