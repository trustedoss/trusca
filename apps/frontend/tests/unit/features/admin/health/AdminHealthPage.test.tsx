/**
 * AdminHealthPage — unit tests.
 *
 * Coverage targets:
 *   - Renders one card per component, locale-agnostic via data-component +
 *     data-status attributes.
 *   - Loading skeletons appear before the query resolves.
 *   - Page-level error alert when the query rejects.
 *   - Refresh button calls refetch (re-issues the query).
 *   - Optional `value` field renders below `detail`.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminHealthPage } from "@/features/admin/health/AdminHealthPage";

vi.mock("@/features/admin/health/api/adminHealthApi", async () => {
  return {
    getAdminHealth: vi.fn(),
  };
});

// W6-#43e: AdminHealthPage now mounts the Trivy DB panel inside the same
// scroll container, so the page test needs to stub the new query too —
// otherwise the panel tries to make a real network call against the JSDOM
// environment.
vi.mock("@/features/admin/health/api/adminTrivyHealthApi", async () => {
  return {
    getAdminTrivyHealth: vi.fn().mockResolvedValue({
      last_update: null,
      next_refresh_at: null,
      vuln_count: null,
      db_version: null,
      db_size_bytes: null,
      refresh_interval_hours: 168,
      freshness: "unknown",
      cache_dir: "/root/.cache/trivy",
      repository: "ghcr.io/aquasecurity/trivy-db",
    }),
  };
});

import {
  getAdminHealth,
  type HealthComponent,
  type SystemHealthOut,
} from "@/features/admin/health/api/adminHealthApi";

const mockedGet = vi.mocked(getAdminHealth);

function component(
  name: HealthComponent["name"],
  overrides: Partial<HealthComponent> = {},
): HealthComponent {
  return {
    name,
    status: overrides.status ?? "ok",
    detail: overrides.detail ?? null,
    detail_code: overrides.detail_code ?? null,
    detail_params: overrides.detail_params ?? null,
    value: overrides.value ?? null,
  };
}

function fixture(components: HealthComponent[]): SystemHealthOut {
  return { components, updated_at: "2026-05-08T00:00:00Z" };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AdminHealthPage />
    </QueryClientProvider>,
  );
}

describe("AdminHealthPage", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("renders one card per backend component", async () => {
    mockedGet.mockResolvedValue(
      fixture([
        component("postgres"),
        component("redis"),
        component("celery", { status: "degraded" }),
        component("disk", { status: "down" }),
        component("active_scans", { value: 4 }),
        component("last_24h_errors", { value: 0 }),
      ]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("admin-health-card")).toHaveLength(6);
    });
    expect(
      document.querySelector('[data-component="celery"]')?.getAttribute(
        "data-status",
      ),
    ).toBe("degraded");
    expect(
      document.querySelector('[data-component="disk"]')?.getAttribute(
        "data-status",
      ),
    ).toBe("down");
  });

  it("renders skeletons while the query is loading", () => {
    mockedGet.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getAllByTestId("admin-health-card-skeleton")).toHaveLength(6);
  });

  it("renders the page-level error alert when the query fails", async () => {
    mockedGet.mockRejectedValue(new Error("boom"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-health-error")).toBeInTheDocument();
    });
  });

  it("renders the value row when the component carries one", async () => {
    mockedGet.mockResolvedValue(
      fixture([component("active_scans", { value: 4 })]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-health-value")).toHaveTextContent("4");
    });
  });

  it("refresh button re-issues the query", async () => {
    mockedGet.mockResolvedValue(fixture([component("postgres")]));
    renderPage();
    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledTimes(1);
    });
    await userEvent.click(screen.getByTestId("admin-health-refresh"));
    await waitFor(() => {
      expect(mockedGet.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });

  // G0-5 backlog — "no workers responded" used to sit untranslated in the
  // middle of an otherwise Korean page. The backend now names the states it
  // produces itself; these three cases are the whole contract.
  describe("detail line", () => {
    it("translates a named state instead of showing the backend's English", async () => {
      mockedGet.mockResolvedValue(
        fixture([
          component("celery", {
            status: "down",
            detail: "no workers responded",
            detail_code: "celery.no_workers",
            value: 0,
          }),
        ]),
      );
      renderPage();
      await waitFor(() => {
        expect(screen.getByTestId("admin-health-detail")).toHaveTextContent(
          "No workers responded",
        );
      });
      expect(screen.getByTestId("admin-health-detail")).toHaveAttribute(
        "data-detail-code",
        "celery.no_workers",
      );
    });

    it("interpolates the params a named state carries", async () => {
      mockedGet.mockResolvedValue(
        fixture([
          component("active_scans", {
            detail: "7 scan(s) queued or running",
            detail_code: "active_scans.count",
            detail_params: { count: 7 },
            value: 7,
          }),
        ]),
      );
      renderPage();
      await waitFor(() => {
        expect(screen.getByTestId("admin-health-detail")).toHaveTextContent(
          "7 scan(s) queued or running",
        );
      });
    });

    it("renders a probe exception verbatim — it has no code and must not be translated", async () => {
      mockedGet.mockResolvedValue(
        fixture([
          component("redis", {
            status: "down",
            detail: "ConnectionError: redis down",
          }),
        ]),
      );
      renderPage();
      await waitFor(() => {
        expect(screen.getByTestId("admin-health-detail")).toHaveTextContent(
          "ConnectionError: redis down",
        );
      });
      expect(screen.getByTestId("admin-health-detail")).not.toHaveAttribute(
        "data-detail-code",
      );
    });

    it("falls back to the English prose for a code this build does not know", async () => {
      // A backend deployed ahead of the frontend. Rendering the raw key would
      // be worse than rendering the sentence the backend already wrote.
      mockedGet.mockResolvedValue(
        fixture([
          component("celery", {
            status: "degraded",
            detail: "3 workers are draining",
            detail_code: "celery.draining_from_a_future_release",
          }),
        ]),
      );
      renderPage();
      await waitFor(() => {
        expect(screen.getByTestId("admin-health-detail")).toHaveTextContent(
          "3 workers are draining",
        );
      });
    });
  });
});
