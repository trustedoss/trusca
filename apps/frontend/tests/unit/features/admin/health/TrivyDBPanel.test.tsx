/**
 * TrivyDBPanel — unit tests (W6-#43e).
 *
 * Coverage targets:
 *   - Fresh state renders 4 KPI tiles + emerald freshness badge.
 *   - Stale state renders amber badge.
 *   - Very-stale state renders red badge.
 *   - Empty state (last_update === null) renders the EmptyState primitive +
 *     metadata footer with cache_dir / repository.
 *   - Empty state (freshness === "unknown") follows the same branch.
 *   - Vuln count thousands separator (432,187).
 *   - Last-update tile carries the absolute ISO in `title` for tooltip.
 *   - Loading skeletons appear before the query resolves.
 *   - Page-level error alert renders when the query rejects.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TrivyDBPanel } from "@/features/admin/health/TrivyDBPanel";

vi.mock("@/features/admin/health/api/adminTrivyHealthApi", async () => {
  return {
    getAdminTrivyHealth: vi.fn(),
  };
});

import {
  getAdminTrivyHealth,
  type TrivyDbStatus,
} from "@/features/admin/health/api/adminTrivyHealthApi";

const mockedGet = vi.mocked(getAdminTrivyHealth);

const REFERENCE_NOW = new Date("2026-05-28T03:14:00Z").getTime();

function statusFixture(overrides: Partial<TrivyDbStatus> = {}): TrivyDbStatus {
  return {
    last_update: "2026-05-27T03:14:00Z",
    next_refresh_at: "2026-06-03T03:14:00Z",
    vuln_count: 432_187,
    db_version: "trivy-db schema v2",
    db_size_bytes: 350_487_632,
    refresh_interval_hours: 168,
    freshness: "fresh",
    cache_dir: "/root/.cache/trivy",
    repository: "ghcr.io/aquasecurity/trivy-db",
    ...overrides,
  };
}

function renderPanel(now: number = REFERENCE_NOW) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TrivyDBPanel now={now} />
    </QueryClientProvider>,
  );
}

describe("TrivyDBPanel", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("renders the fresh state with KPI grid and emerald badge", async () => {
    mockedGet.mockResolvedValue(statusFixture());
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId("admin-trivy-db-panel")).toHaveAttribute(
        "data-status",
        "fresh",
      );
    });
    const badge = screen.getByTestId("admin-trivy-db-freshness-badge");
    expect(badge).toHaveAttribute("data-freshness", "fresh");
    // KPI tiles present
    expect(
      screen.getByTestId("admin-trivy-db-kpi-last-update"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("admin-trivy-db-kpi-vuln-count"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("admin-trivy-db-kpi-db-version"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("admin-trivy-db-kpi-next-refresh"),
    ).toBeInTheDocument();
  });

  it("formats the vuln count with thousands separators", async () => {
    mockedGet.mockResolvedValue(statusFixture({ vuln_count: 432_187 }));
    renderPanel();
    const vulnTile = await screen.findByTestId(
      "admin-trivy-db-kpi-vuln-count",
    );
    // We assert against both EN and KO comma renderings — both locales use
    // `,` as the thousands separator under default Intl.
    expect(vulnTile.textContent).toMatch(/432[,.\s]187/);
  });

  it("carries the absolute ISO in the last-update tile title attribute", async () => {
    mockedGet.mockResolvedValue(statusFixture());
    renderPanel();
    const tile = await screen.findByTestId(
      "admin-trivy-db-kpi-last-update",
    );
    // Tooltip ISO surfaced via title attribute on the value <p>. The relative
    // wording varies by locale (`yesterday` / `1 day ago` / `어제` / ...),
    // so we go directly to the paragraph that carries the title attribute
    // rather than matching on the displayed text.
    const tooltipBearer = within(tile)
      .getAllByText((_content, el) => el?.tagName === "P")
      .find((p) => p.getAttribute("title") != null);
    expect(tooltipBearer).toBeDefined();
    expect(tooltipBearer?.getAttribute("title")).toBe(
      "2026-05-27T03:14:00Z",
    );
  });

  it("renders the stale state with an amber badge", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({
        freshness: "stale",
        last_update: "2026-05-18T03:14:00Z", // 10 days ago
      }),
    );
    renderPanel();
    const badge = await screen.findByTestId(
      "admin-trivy-db-freshness-badge",
    );
    expect(badge).toHaveAttribute("data-freshness", "stale");
    expect(badge.className).toMatch(/amber/);
  });

  it("renders the very-stale state with a red badge", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({
        freshness: "very_stale",
        last_update: "2026-05-01T03:14:00Z", // 27 days ago
      }),
    );
    renderPanel();
    const badge = await screen.findByTestId(
      "admin-trivy-db-freshness-badge",
    );
    expect(badge).toHaveAttribute("data-freshness", "very_stale");
    expect(badge.className).toMatch(/red/);
  });

  it("renders the empty state when last_update is null", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({
        last_update: null,
        next_refresh_at: null,
        vuln_count: null,
        db_version: null,
        db_size_bytes: null,
        freshness: "unknown",
      }),
    );
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId("admin-trivy-db-panel")).toHaveAttribute(
        "data-status",
        "empty",
      );
    });
    expect(screen.getByTestId("admin-trivy-db-empty")).toBeInTheDocument();
    // Metadata footer still surfaces cache + repo so operators see config.
    const footer = screen.getByTestId("admin-trivy-db-footer");
    expect(footer.textContent).toContain("/root/.cache/trivy");
    expect(footer.textContent).toContain("ghcr.io/aquasecurity/trivy-db");
    // No freshness badge in the empty branch.
    expect(
      screen.queryByTestId("admin-trivy-db-freshness-badge"),
    ).not.toBeInTheDocument();
  });

  it("renders the empty state when freshness is unknown but last_update is set", async () => {
    // Degenerate case (service-layer graceful degrade) — last_update could
    // be set but freshness still resolves to ``unknown`` if the classifier
    // hits an edge. We treat the panel uniformly.
    mockedGet.mockResolvedValue(
      statusFixture({ freshness: "unknown" }),
    );
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId("admin-trivy-db-panel")).toHaveAttribute(
        "data-status",
        "empty",
      );
    });
    expect(screen.getByTestId("admin-trivy-db-empty")).toBeInTheDocument();
  });

  it("renders skeletons while the query is loading", () => {
    mockedGet.mockReturnValue(new Promise(() => {}));
    renderPanel();
    expect(
      screen.getAllByTestId("admin-trivy-db-skeleton"),
    ).toHaveLength(4);
  });

  it("renders the page-level error alert when the query fails", async () => {
    mockedGet.mockRejectedValue(new Error("boom"));
    renderPanel();
    await waitFor(() => {
      expect(
        screen.getByTestId("admin-trivy-db-error"),
      ).toBeInTheDocument();
    });
  });

  it("surfaces the cache_dir and repository in the metadata footer", async () => {
    mockedGet.mockResolvedValue(
      statusFixture({
        cache_dir: "/var/lib/trivy",
        repository: "registry.internal/mirror/trivy-db",
      }),
    );
    renderPanel();
    const footer = await screen.findByTestId("admin-trivy-db-footer");
    expect(footer.textContent).toContain("/var/lib/trivy");
    expect(footer.textContent).toContain("registry.internal/mirror/trivy-db");
  });
});
