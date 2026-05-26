/**
 * AdminDTPage — unit tests.
 *
 * Mocks the wire layer so the page's behaviour (status card, orphan list,
 * cleanup confirm flow, force health probe) is observable without a backend.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminDTPage } from "@/features/admin/dt/AdminDTPage";

vi.mock("@/features/admin/dt/api/adminDTApi", async () => {
  return {
    getDTStatus: vi.fn(),
    listDTOrphans: vi.fn(),
    cleanupDTOrphans: vi.fn(),
    forceDTHealthCheck: vi.fn(),
    resetDTBreaker: vi.fn(),
  };
});

import {
  cleanupDTOrphans,
  forceDTHealthCheck,
  getDTStatus,
  listDTOrphans,
  resetDTBreaker,
  type BreakerResetOut,
  type DTOrphanListPage,
  type DTStatus,
  type HealthProbeOut,
  type OrphanCleanupEnqueued,
} from "@/features/admin/dt/api/adminDTApi";

const mockedStatus = vi.mocked(getDTStatus);
const mockedOrphans = vi.mocked(listDTOrphans);
const mockedCleanup = vi.mocked(cleanupDTOrphans);
const mockedProbe = vi.mocked(forceDTHealthCheck);
const mockedReset = vi.mocked(resetDTBreaker);

function statusFixture(overrides: Partial<DTStatus> = {}): DTStatus {
  return {
    state: "closed",
    fail_count: 0,
    opened_at: null,
    last_check_at: "2026-05-08T00:00:00Z",
    version: "4.13.2",
    vulnerability_count: 274321,
    last_error: null,
    auto_restart_attempted: false,
    ...overrides,
  };
}

function orphansFixture(count: number = 2): DTOrphanListPage {
  return {
    items: Array.from({ length: count }).map((_, i) => ({
      dt_project_uuid: `uuid-${i}`,
      dt_project_name: `orphan-${i}`,
      dt_project_version: i % 2 === 0 ? "1.0" : null,
    })),
    total: count,
    has_more: false,
  };
}

function probeFixture(overrides: Partial<HealthProbeOut> = {}): HealthProbeOut {
  return {
    healthy: true,
    state_before: "closed",
    state_after: "closed",
    fail_count: 0,
    auto_restart_attempted: false,
    error: null,
    checked_at: "2026-05-08T00:00:01Z",
    ...overrides,
  };
}

function cleanupFixture(): OrphanCleanupEnqueued {
  return {
    task_id: "task-abc",
    enqueued_at: "2026-05-08T00:00:02Z",
    count: 2,
  };
}

function resetFixture(overrides: Partial<BreakerResetOut> = {}): BreakerResetOut {
  return {
    state_before: "open",
    state_after: "closed",
    fail_count_before: 5,
    reset_at: "2026-05-10T00:00:00Z",
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AdminDTPage />
    </QueryClientProvider>,
  );
}

describe("AdminDTPage", () => {
  beforeEach(() => {
    mockedStatus.mockReset();
    mockedOrphans.mockReset();
    mockedCleanup.mockReset();
    mockedProbe.mockReset();
    mockedReset.mockReset();
  });

  it("renders the status card and orphan list once both queries resolve", async () => {
    mockedStatus.mockResolvedValue(statusFixture());
    mockedOrphans.mockResolvedValue(orphansFixture(3));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-dt-status-card")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getAllByTestId("admin-dt-orphan-row")).toHaveLength(3);
    });
    expect(screen.getByTestId("dt-breaker-badge")).toHaveAttribute(
      "data-state",
      "closed",
    );
    expect(screen.getByTestId("dt-breaker-badge")).toHaveAttribute(
      "data-tone",
      "ok",
    );
  });

  it("shows the vuln-DB count and NO empty-warning when the mirror is populated (#35)", async () => {
    mockedStatus.mockResolvedValue(statusFixture({ vulnerability_count: 274321 }));
    mockedOrphans.mockResolvedValue(orphansFixture(0));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-dt-vuln-count")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-dt-vuln-count").textContent).toContain(
      "274,321",
    );
    expect(screen.queryByTestId("admin-dt-vuln-db-empty")).not.toBeInTheDocument();
  });

  it("warns the operator when DT's vulnerability DB is empty (#35 silent-zero)", async () => {
    mockedStatus.mockResolvedValue(statusFixture({ vulnerability_count: 0 }));
    mockedOrphans.mockResolvedValue(orphansFixture(0));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-dt-vuln-db-empty")).toBeInTheDocument();
    });
  });

  it("renders the breaker badge with degraded tone when state=half_open", async () => {
    mockedStatus.mockResolvedValue(statusFixture({ state: "half_open" }));
    mockedOrphans.mockResolvedValue(orphansFixture(0));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("dt-breaker-badge")).toHaveAttribute(
        "data-tone",
        "degraded",
      );
    });
  });

  it("renders the breaker badge with down tone when state=open", async () => {
    mockedStatus.mockResolvedValue(statusFixture({ state: "open" }));
    mockedOrphans.mockResolvedValue(orphansFixture(0));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("dt-breaker-badge")).toHaveAttribute(
        "data-tone",
        "down",
      );
    });
  });

  it("renders the empty-orphan state when the catalog is clean", async () => {
    mockedStatus.mockResolvedValue(statusFixture());
    mockedOrphans.mockResolvedValue(orphansFixture(0));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-dt-orphans-empty")).toBeInTheDocument();
    });
  });

  it("renders an error alert when the orphans query fails", async () => {
    mockedStatus.mockResolvedValue(statusFixture());
    mockedOrphans.mockRejectedValue(new Error("boom"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-dt-orphans-error")).toBeInTheDocument();
    });
  });

  it("force-probe button calls the API and surfaces a success toast", async () => {
    mockedStatus.mockResolvedValue(statusFixture());
    mockedOrphans.mockResolvedValue(orphansFixture(0));
    mockedProbe.mockResolvedValue(probeFixture());
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-dt-force-probe")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-dt-force-probe"));
    await waitFor(() => {
      expect(mockedProbe).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      const toast = screen.getByTestId("admin-toast");
      expect(toast).toHaveAttribute("data-tone", "success");
      expect(toast).toHaveAttribute("data-toast-key", "health_checked");
    });
  });

  it("clean up all → confirm strip → confirm fires the cleanup mutation", async () => {
    mockedStatus.mockResolvedValue(statusFixture());
    mockedOrphans.mockResolvedValue(orphansFixture(2));
    mockedCleanup.mockResolvedValue(cleanupFixture());
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("admin-dt-orphan-row")).toHaveLength(2);
    });
    await userEvent.click(screen.getByTestId("admin-dt-cleanup-all"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-dt-confirm-strip")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-dt-confirm-strip")).toHaveAttribute(
      "data-kind",
      "cleanup_all",
    );
    await userEvent.click(screen.getByTestId("admin-dt-confirm-ok"));
    await waitFor(() => {
      expect(mockedCleanup).toHaveBeenCalledWith({});
    });
    await waitFor(() => {
      const toast = screen.getByTestId("admin-toast");
      expect(toast).toHaveAttribute("data-toast-key", "cleanup_enqueued");
    });
  });

  it("A4: reset breaker is disabled while state=closed", async () => {
    mockedStatus.mockResolvedValue(statusFixture({ state: "closed" }));
    mockedOrphans.mockResolvedValue(orphansFixture(0));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-dt-reset-breaker")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-dt-reset-breaker")).toBeDisabled();
  });

  it("A4: reset breaker enables on OPEN, confirm strip → reset mutation + toast", async () => {
    mockedStatus.mockResolvedValue(statusFixture({ state: "open", fail_count: 5 }));
    mockedOrphans.mockResolvedValue(orphansFixture(0));
    mockedReset.mockResolvedValue(resetFixture());
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-dt-reset-breaker")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-dt-reset-breaker")).not.toBeDisabled();

    await userEvent.click(screen.getByTestId("admin-dt-reset-breaker"));
    await waitFor(() => {
      expect(
        screen.getByTestId("admin-dt-reset-confirm-strip"),
      ).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("admin-dt-reset-confirm-ok"));
    await waitFor(() => {
      expect(mockedReset).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      const toast = screen.getByTestId("admin-toast");
      expect(toast).toHaveAttribute("data-tone", "success");
      expect(toast).toHaveAttribute("data-toast-key", "breaker_reset");
    });
  });

  it("A4: reset confirm cancel dismisses the strip without firing the mutation", async () => {
    mockedStatus.mockResolvedValue(statusFixture({ state: "half_open" }));
    mockedOrphans.mockResolvedValue(orphansFixture(0));
    mockedReset.mockResolvedValue(resetFixture());
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-dt-reset-breaker")).not.toBeDisabled();
    });
    await userEvent.click(screen.getByTestId("admin-dt-reset-breaker"));
    await waitFor(() => {
      expect(
        screen.getByTestId("admin-dt-reset-confirm-strip"),
      ).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-dt-reset-confirm-cancel"));
    await waitFor(() => {
      expect(
        screen.queryByTestId("admin-dt-reset-confirm-strip"),
      ).not.toBeInTheDocument();
    });
    expect(mockedReset).not.toHaveBeenCalled();
  });

  it("A4: reset error surfaces a toast with the dt_breaker_already_closed extension", async () => {
    mockedStatus.mockResolvedValue(statusFixture({ state: "open" }));
    mockedOrphans.mockResolvedValue(orphansFixture(0));
    const { ProblemError } = await import("@/lib/problem");
    mockedReset.mockRejectedValue(
      new ProblemError("Breaker Already Closed", {
        status: 409,
        title: "Breaker Already Closed",
        detail: "DT breaker is already CLOSED",
        problem: {
          type: "https://docs.trustedoss.io/errors/dt-breaker-already-closed",
          title: "Breaker Already Closed",
          status: 409,
          detail: "DT breaker is already CLOSED",
          dt_breaker_already_closed: true,
        },
      }),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-dt-reset-breaker")).not.toBeDisabled();
    });
    await userEvent.click(screen.getByTestId("admin-dt-reset-breaker"));
    await userEvent.click(screen.getByTestId("admin-dt-reset-confirm-ok"));
    await waitFor(() => {
      const toast = screen.getByTestId("admin-toast");
      expect(toast).toHaveAttribute("data-tone", "error");
      expect(toast).toHaveAttribute("data-toast-key", "dt_breaker_already_closed");
    });
  });

  it("clean up selected requires at least one row checked", async () => {
    mockedStatus.mockResolvedValue(statusFixture());
    mockedOrphans.mockResolvedValue(orphansFixture(2));
    mockedCleanup.mockResolvedValue(cleanupFixture());
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("admin-dt-orphan-row")).toHaveLength(2);
    });
    // No selection — button is disabled.
    expect(screen.getByTestId("admin-dt-cleanup-selected")).toBeDisabled();

    // Tick the first row's checkbox.
    const firstCheckbox = screen.getAllByTestId("admin-dt-orphan-checkbox")[0];
    await userEvent.click(firstCheckbox);
    expect(screen.getByTestId("admin-dt-cleanup-selected")).not.toBeDisabled();

    await userEvent.click(screen.getByTestId("admin-dt-cleanup-selected"));
    await userEvent.click(screen.getByTestId("admin-dt-confirm-ok"));
    await waitFor(() => {
      expect(mockedCleanup).toHaveBeenCalledWith({
        dt_project_uuids: ["uuid-0"],
      });
    });
  });
});
