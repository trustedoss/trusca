/**
 * KevFeedPanel — unit tests (Phase C / C2).
 *
 * Coverage targets (mirrors TrivyDBPanel.test.tsx):
 *   - Synced state renders 4 KPI tiles + emerald status badge + feed_host
 *     footer, with raw wire values on the `data-value` e2e anchors.
 *   - Skipped state renders the amber badge + the skipped_reason line.
 *   - Never-ran (all-null row) renders the EmptyState + footer, no badge.
 *   - Disabled feed renders the muted "Disabled" badge (outranking results).
 *   - Loading skeletons appear before the query resolves.
 *   - Page-level error alert renders when the query rejects.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KevFeedPanel } from "@/features/admin/health/KevFeedPanel";

vi.mock("@/features/admin/health/api/adminKevHealthApi", async () => {
  return {
    getAdminKevHealth: vi.fn(),
  };
});

import {
  getAdminKevHealth,
  type KevFeedStatus,
} from "@/features/admin/health/api/adminKevHealthApi";

const mockedGet = vi.mocked(getAdminKevHealth);

const REFERENCE_NOW = new Date("2026-07-03T09:00:00Z").getTime();

function statusFixture(overrides: Partial<KevFeedStatus> = {}): KevFeedStatus {
  return {
    enabled: true,
    last_synced_at: "2026-07-02T03:00:00Z",
    last_attempt_at: "2026-07-02T03:00:00Z",
    last_result: "synced",
    skipped_reason: null,
    feed_count: 1_423,
    listed: 12,
    delisted: 3,
    duration_ms: 5_400,
    kev_flagged_total: 87,
    next_refresh_at: "2026-07-04T03:00:00Z",
    feed_host: "www.cisa.gov",
    ...overrides,
  };
}

function renderPanel(now: number = REFERENCE_NOW) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <KevFeedPanel now={now} />
    </QueryClientProvider>,
  );
}

describe("KevFeedPanel", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("renders the synced state with KPI grid and emerald badge", async () => {
    mockedGet.mockResolvedValue(statusFixture());
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId("kev-feed-panel")).toHaveAttribute(
        "data-status",
        "synced",
      );
    });
    const badge = screen.getByTestId("kev-feed-status-badge");
    expect(badge).toHaveAttribute("data-status", "synced");
    expect(badge.className).toMatch(/emerald/);
    // KPI tiles present with raw wire values on data-value e2e anchors.
    expect(
      screen.getByTestId("kev-feed-kpi-last-synced"),
    ).toHaveAttribute("data-value", "2026-07-02T03:00:00Z");
    expect(
      screen.getByTestId("kev-feed-kpi-flagged-total"),
    ).toHaveAttribute("data-value", "87");
    expect(
      screen.getByTestId("kev-feed-kpi-listed-delisted"),
    ).toHaveAttribute("data-value", "12/3");
    expect(
      screen.getByTestId("kev-feed-kpi-next-refresh"),
    ).toHaveAttribute("data-value", "2026-07-04T03:00:00Z");
    // Listed/delisted rendered as a paired delta, color-free.
    expect(
      screen.getByTestId("kev-feed-kpi-listed-delisted").textContent,
    ).toContain("+12 / −3");
    // No skipped-reason line in the synced branch.
    expect(
      screen.queryByTestId("kev-feed-skipped-reason"),
    ).not.toBeInTheDocument();
  });

  it("renders the skipped state with an amber badge and the reason line", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({
        last_result: "skipped",
        skipped_reason: "feed not modified since last sync",
        listed: null,
        delisted: null,
      }),
    );
    renderPanel();
    const badge = await screen.findByTestId("kev-feed-status-badge");
    expect(badge).toHaveAttribute("data-status", "skipped");
    expect(badge.className).toMatch(/amber/);
    // The reason is spelled out — color is never the only signal.
    expect(
      screen.getByTestId("kev-feed-skipped-reason").textContent,
    ).toContain("feed not modified since last sync");
    // Null deltas render the em-dash instead of a fabricated +0 / −0.
    expect(
      screen.getByTestId("kev-feed-kpi-listed-delisted").textContent,
    ).toContain("—");
  });

  it("renders the never-ran state as an EmptyState with the feed host footer", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({
        last_synced_at: null,
        last_attempt_at: null,
        last_result: null,
        skipped_reason: null,
        feed_count: null,
        listed: null,
        delisted: null,
        duration_ms: null,
        kev_flagged_total: 0,
        next_refresh_at: null,
      }),
    );
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId("kev-feed-panel")).toHaveAttribute(
        "data-status",
        "empty",
      );
    });
    expect(screen.getByTestId("kev-feed-empty")).toBeInTheDocument();
    // No result badge — there is no run to describe yet.
    expect(
      screen.queryByTestId("kev-feed-status-badge"),
    ).not.toBeInTheDocument();
    // Footer still surfaces the effective feed host config.
    expect(
      screen.getByTestId("kev-feed-footer").textContent,
    ).toContain("www.cisa.gov");
  });

  it("renders the muted Disabled badge when the feed is switched off", async () => {
    mockedGet.mockResolvedValue(statusFixture({ enabled: false }));
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId("kev-feed-panel")).toHaveAttribute(
        "data-status",
        "disabled",
      );
    });
    const badge = screen.getByTestId("kev-feed-status-badge");
    expect(badge).toHaveAttribute("data-status", "disabled");
    expect(badge.className).toMatch(/muted/);
  });

  it("keeps the Disabled badge in the never-ran branch (config outranks empty)", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({
        enabled: false,
        last_synced_at: null,
        last_attempt_at: null,
        last_result: null,
        feed_count: null,
        listed: null,
        delisted: null,
        duration_ms: null,
        kev_flagged_total: 0,
        next_refresh_at: null,
      }),
    );
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId("kev-feed-panel")).toHaveAttribute(
        "data-status",
        "disabled",
      );
    });
    expect(screen.getByTestId("kev-feed-empty")).toBeInTheDocument();
    expect(
      screen.getByTestId("kev-feed-status-badge"),
    ).toHaveAttribute("data-status", "disabled");
  });

  it("renders skeletons while the query is loading", () => {
    mockedGet.mockReturnValue(new Promise(() => {}));
    renderPanel();
    expect(screen.getAllByTestId("kev-feed-skeleton")).toHaveLength(4);
  });

  it("renders the page-level error alert when the query fails", async () => {
    mockedGet.mockRejectedValue(new Error("boom"));
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId("kev-feed-error")).toBeInTheDocument();
    });
  });
});
