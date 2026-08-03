/**
 * The action queue panel.
 *
 * The case that matters most here is the error state. A queue that failed to
 * load and a queue with nothing in it look identical if you are careless, and
 * of the two possible mistakes — telling someone there is work when there is
 * none, and telling them there is none when nobody knows — the second is the
 * one that lets a KEV deadline pass. So the error path is asserted to say so
 * explicitly rather than falling through to the all-clear message.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionQueuePanel } from "@/features/dashboard/ActionQueuePanel";
import type { ActionQueue } from "@/features/dashboard/api/actionQueue";

// Mocked at the HTTP client rather than at the query function: the hook
// closes over its own module's export, so stubbing that export leaves the
// hook calling the original. Going through `api.get` also means the query
// key, the error path and the response shape are all still real.
const apiGet = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ api: { get: apiGet } }));

function resolveQueue(queue: ActionQueue) {
  apiGet.mockResolvedValue({ data: queue });
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ActionQueuePanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const EMPTY: ActionQueue = {
  pending_approvals: 0,
  kev_sla: { overdue: 0, due_soon: 0 },
  gate_blocked: [],
  stale_projects: [],
};

afterEach(() => {
  vi.clearAllMocks();
});

describe("ActionQueuePanel", () => {
  it("says nothing is waiting when every bucket is empty", async () => {
    resolveQueue(EMPTY);

    renderPanel();

    expect(await screen.findByTestId("action-queue-clear")).toBeInTheDocument();
    expect(screen.getByTestId("action-queue-approvals")).toHaveAttribute(
      "data-count",
      "0",
    );
  });

  it("does not claim an empty queue when the request failed", async () => {
    apiGet.mockRejectedValue(new Error("boom"));

    renderPanel();

    expect(await screen.findByTestId("action-queue-error")).toBeInTheDocument();
    // The distinction this test exists for.
    expect(screen.queryByTestId("action-queue-clear")).toBeNull();
    expect(screen.queryByTestId("action-queue")).toBeNull();
  });

  it("surfaces counts and the projects behind them", async () => {
    resolveQueue({
      pending_approvals: 3,
      kev_sla: { overdue: 2, due_soon: 1 },
      gate_blocked: [
        {
          project_id: "p-1",
          project_name: "payments-api",
          scan_id: "s-1",
          critical_cve_count: 4,
          forbidden_license_count: 1,
          epss_gate_count: 0,
        },
      ],
      stale_projects: [
        {
          project_id: "p-2",
          project_name: "legacy-batch",
          last_succeeded_at: null,
        },
      ],
    } satisfies ActionQueue);

    renderPanel();

    expect(await screen.findByTestId("action-queue")).toBeInTheDocument();
    expect(screen.getByTestId("action-queue-approvals")).toHaveAttribute(
      "data-count",
      "3",
    );
    // KEV shows the total; the split lives in the hint so one number does not
    // have to mean two things.
    expect(screen.getByTestId("action-queue-kev")).toHaveAttribute(
      "data-count",
      "3",
    );
    expect(screen.getByTestId("action-queue-gate-row-p-1")).toHaveAttribute(
      "href",
      "/projects/p-1",
    );
    expect(screen.getByTestId("action-queue-stale-row-p-2")).toBeInTheDocument();
    expect(screen.queryByTestId("action-queue-clear")).toBeNull();
  });

  it("labels a project that has never been scanned rather than showing a blank", async () => {
    resolveQueue({
      ...EMPTY,
      stale_projects: [
        {
          project_id: "p-3",
          project_name: "registered-and-forgotten",
          last_succeeded_at: null,
        },
      ],
    } satisfies ActionQueue);

    renderPanel();

    const row = await screen.findByTestId("action-queue-stale-row-p-3");
    // A missing timestamp is information, not an absence: it is how a project
    // registered and then ignored is distinguished from one merely overdue.
    expect(row.textContent).toContain("Never scanned");
  });
});
