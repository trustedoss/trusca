/**
 * MaliciousPanel — unit tests (#26, MAL-2b). Mirrors EolPanel.test.tsx.
 *
 * The panel's job is to answer "did anything I already ship turn out to be
 * malicious", so the tests lean on the two places that question is answered:
 * the `newly_flagged` tile and the note above it.
 *
 * Staleness is deliberately not recomputed here. The backend owns the window
 * (60 days, tunable per deployment) and sends `snapshot_stale`; a panel that
 * re-derived it would disagree with the API the moment an operator tuned it.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MaliciousPanel } from "@/features/admin/health/MaliciousPanel";

vi.mock("@/features/admin/health/api/adminMaliciousHealthApi", async () => {
  return {
    getAdminMaliciousHealth: vi.fn(),
  };
});

import {
  getAdminMaliciousHealth,
  type MaliciousStatus,
} from "@/features/admin/health/api/adminMaliciousHealthApi";

const mockedGet = vi.mocked(getAdminMaliciousHealth);

function statusFixture(overrides: Partial<MaliciousStatus> = {}): MaliciousStatus {
  return {
    enabled: true,
    refresh_enabled: false,
    snapshot_date: "2026-08-03",
    snapshot_stale: false,
    purl_count: 232747,
    ecosystems: ["npm", "PyPI", "crates.io"],
    flagged_total: 0,
    last_synced_at: null,
    last_attempt_at: "2026-08-03T02:40:00Z",
    last_result: "skipped",
    skipped_reason: "refresh_disabled",
    stamped: 23051,
    newly_flagged: 0,
    next_refresh_at: "2026-08-10T02:40:00Z",
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MaliciousPanel />
    </QueryClientProvider>,
  );
}

describe("MaliciousPanel", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("treats a fetch-disabled run as OK, not as a skip", async () => {
    // `refresh_disabled` is the default posture, not a fault: the re-stamp
    // half still ran. Badging it amber would leave every stock install
    // permanently warning about a setting it was shipped with.
    mockedGet.mockResolvedValue(statusFixture());
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("malicious-panel")).toHaveAttribute(
        "data-status",
        "ok",
      ),
    );
    expect(screen.getByTestId("malicious-kpi-snapshot")).toHaveAttribute(
      "data-value",
      "2026-08-03",
    );
    expect(screen.getByTestId("malicious-kpi-next-tick")).toHaveAttribute(
      "data-value",
      "2026-08-10T02:40:00Z",
    );
    expect(screen.queryByTestId("malicious-newly-flagged-note")).toBeNull();
  });

  it("surfaces newly flagged packages above the grid", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({ newly_flagged: 3, flagged_total: 3, last_result: "synced" }),
    );
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("malicious-newly-flagged-note")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("malicious-kpi-newly-flagged")).toHaveAttribute(
      "data-value",
      "3",
    );
    // The wording must survive any count — i18next v4 never consults a
    // `_plural` key, so the string carries no number agreement.
    expect(screen.getByTestId("malicious-newly-flagged-note")).toHaveTextContent(
      /\b3\b/,
    );
  });

  it("shows the stale badge from the API flag rather than recomputing it", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({ snapshot_date: "2026-01-02", snapshot_stale: true }),
    );
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("malicious-panel")).toHaveAttribute(
        "data-status",
        "stale",
      ),
    );
    expect(screen.getByTestId("malicious-stale-note")).toBeInTheDocument();
  });

  it("badges a genuine skip amber, unlike the fetch-disabled default", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({ skipped_reason: "snapshot_missing" }),
    );
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("malicious-panel")).toHaveAttribute(
        "data-status",
        "skipped",
      ),
    );
  });

  it("lets disabled outrank a stale snapshot", async () => {
    // Nothing is evaluating packages, so the snapshot's age is not the story.
    mockedGet.mockResolvedValue(
      statusFixture({ enabled: false, snapshot_stale: true }),
    );
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("malicious-panel")).toHaveAttribute(
        "data-status",
        "disabled",
      ),
    );
    expect(screen.queryByTestId("malicious-stale-note")).toBeNull();
  });

  it("keeps the grid with dashes when the beat has never run", async () => {
    // Snapshot fields ship with the release; only beat-derived tiles are null.
    mockedGet.mockResolvedValue(
      statusFixture({
        flagged_total: null,
        stamped: null,
        newly_flagged: null,
        last_attempt_at: null,
        last_result: null,
        skipped_reason: null,
      }),
    );
    renderPanel();

    await waitFor(() =>
      expect(screen.getByTestId("malicious-kpi-snapshot")).toHaveAttribute(
        "data-value",
        "2026-08-03",
      ),
    );
    expect(screen.getByTestId("malicious-kpi-newly-flagged")).toHaveAttribute(
      "data-value",
      "",
    );
    expect(screen.queryByTestId("malicious-newly-flagged-note")).toBeNull();
  });

  it("renders skeletons while loading and an alert on failure", async () => {
    mockedGet.mockReturnValue(new Promise(() => {}));
    const { unmount } = renderPanel();
    expect(await screen.findAllByTestId("malicious-skeleton")).toHaveLength(4);
    unmount();

    mockedGet.mockRejectedValue(new Error("boom"));
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("malicious-error")).toBeInTheDocument(),
    );
  });
});
