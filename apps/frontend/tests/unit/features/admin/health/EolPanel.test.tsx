/**
 * EolPanel — unit tests (Phase M / PR M-3).
 *
 * Coverage targets (mirrors KevFeedPanel.test.tsx):
 *   - OK state renders 4 KPI tiles + emerald badge + origin/refresh footer,
 *     raw wire values on the `data-value` e2e anchors.
 *   - Stale snapshot (>180 days) escalates to the amber "stale" badge + the
 *     stale note with the day count.
 *   - Skipped fetch renders the amber badge + the raw skip reason.
 *   - Disabled flagging renders the muted badge (outranks everything).
 *   - Beat-never-ran keeps the KPI grid (vendored snapshot side is always
 *     populated) with dashes for the beat-derived tiles — no EmptyState.
 *   - Loading skeletons + page-level error alert.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EolPanel } from "@/features/admin/health/EolPanel";

vi.mock("@/features/admin/health/api/adminEolHealthApi", async () => {
  return {
    getAdminEolHealth: vi.fn(),
  };
});

import {
  getAdminEolHealth,
  type EolStatus,
} from "@/features/admin/health/api/adminEolHealthApi";

const mockedGet = vi.mocked(getAdminEolHealth);

const REFERENCE_NOW = new Date("2026-07-12T09:00:00Z").getTime();

function statusFixture(overrides: Partial<EolStatus> = {}): EolStatus {
  return {
    enabled: true,
    refresh_enabled: false,
    snapshot_date: "2026-07-11",
    snapshot_origin: "vendored",
    rule_count: 11,
    product_count: 10,
    eol_flagged_total: 4,
    last_synced_at: null,
    last_attempt_at: "2026-07-12T02:15:00Z",
    last_result: "skipped",
    skipped_reason: null,
    stamped: 7,
    cleared: 1,
    duration_ms: 900,
    next_refresh_at: "2026-07-19T02:15:00Z",
    feed_host: "endoflife.date",
    ...overrides,
  };
}

function renderPanel(now: number = REFERENCE_NOW) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <EolPanel now={now} />
    </QueryClientProvider>,
  );
}

describe("EolPanel", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("renders the OK state with KPI grid and emerald badge", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({ last_result: "synced", last_synced_at: "2026-07-12T02:15:05Z" }),
    );
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("eol-panel")).toHaveAttribute("data-status", "ok"),
    );
    expect(screen.getByTestId("eol-status-badge")).toHaveAttribute(
      "data-status",
      "ok",
    );
    expect(screen.getByTestId("eol-kpi-snapshot-date")).toHaveAttribute(
      "data-value",
      "2026-07-11",
    );
    expect(screen.getByTestId("eol-kpi-flagged-total")).toHaveAttribute(
      "data-value",
      "4",
    );
    expect(screen.getByTestId("eol-kpi-stamped-cleared")).toHaveAttribute(
      "data-value",
      "7/1",
    );
    expect(screen.getByTestId("eol-kpi-next-refresh")).toHaveAttribute(
      "data-value",
      "2026-07-19T02:15:00Z",
    );
    expect(screen.getByTestId("eol-footer")).toBeInTheDocument();
  });

  it("escalates a >180-day-old snapshot to the stale badge + note", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({ snapshot_date: "2025-06-01", last_result: "synced" }),
    );
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("eol-panel")).toHaveAttribute(
        "data-status",
        "stale",
      ),
    );
    expect(screen.getByTestId("eol-stale-note")).toBeInTheDocument();
  });

  it("renders the skipped state with the raw reason line", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({ last_result: "skipped", skipped_reason: "feed_unavailable" }),
    );
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("eol-panel")).toHaveAttribute(
        "data-status",
        "skipped",
      ),
    );
    expect(screen.getByTestId("eol-skipped-reason")).toHaveTextContent(
      "feed_unavailable",
    );
  });

  it("disabled flagging outranks every other status", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({ enabled: false, snapshot_date: "2025-01-01" }),
    );
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("eol-panel")).toHaveAttribute(
        "data-status",
        "disabled",
      ),
    );
  });

  it("beat-never-ran keeps the grid with dashes (no EmptyState)", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({
        last_attempt_at: null,
        last_result: null,
        stamped: null,
        cleared: null,
        duration_ms: null,
      }),
    );
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("eol-panel")).toHaveAttribute("data-status", "ok"),
    );
    // Snapshot side still populated; beat side dashes.
    expect(screen.getByTestId("eol-kpi-snapshot-date")).toHaveAttribute(
      "data-value",
      "2026-07-11",
    );
    expect(screen.getByTestId("eol-kpi-stamped-cleared")).not.toHaveAttribute(
      "data-value",
    );
  });

  it("shows loading skeletons before the query resolves", () => {
    mockedGet.mockReturnValue(new Promise(() => {}) as Promise<EolStatus>);
    renderPanel();
    expect(screen.getAllByTestId("eol-skeleton")).toHaveLength(4);
  });

  it("renders the error alert when the query rejects", async () => {
    mockedGet.mockRejectedValue(new Error("boom"));
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("eol-error")).toBeInTheDocument(),
    );
  });
});
