/**
 * AdminDiskPage — unit tests.
 *
 * Coverage targets:
 *   - Renders four cards (or N) with status-derived data attributes.
 *   - The progress bar's `aria-valuenow` mirrors `used_pct`.
 *   - Per-item error renders the alert instead of the gauge.
 *   - Loading skeletons render before the query resolves.
 *   - The query error surfaces the page-level alert.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminDiskPage } from "@/features/admin/disk/AdminDiskPage";

vi.mock("@/features/admin/disk/api/adminDiskApi", async () => {
  return {
    getAdminDisk: vi.fn(),
  };
});

import {
  getAdminDisk,
  type AdminDiskItem,
  type AdminDiskOut,
} from "@/features/admin/disk/api/adminDiskApi";

const mockedGet = vi.mocked(getAdminDisk);

function diskItem(
  name: AdminDiskItem["name"],
  overrides: Partial<AdminDiskItem> = {},
): AdminDiskItem {
  return {
    name,
    path: overrides.path ?? `/${name}`,
    total_bytes: overrides.total_bytes ?? 1024 * 1024 * 1024 * 100,
    used_bytes: overrides.used_bytes ?? 1024 * 1024 * 1024 * 50,
    free_bytes: overrides.free_bytes ?? 1024 * 1024 * 1024 * 50,
    used_pct: overrides.used_pct ?? 50,
    threshold_warning: overrides.threshold_warning ?? 80,
    threshold_critical: overrides.threshold_critical ?? 90,
    status: overrides.status ?? "ok",
    error: overrides.error ?? null,
  };
}

function diskFixture(items: AdminDiskItem[]): AdminDiskOut {
  return { items, collected_at: "2026-05-08T00:00:00Z" };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AdminDiskPage />
    </QueryClientProvider>,
  );
}

describe("AdminDiskPage", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("renders three cards mapped to their backend names", async () => {
    mockedGet.mockResolvedValue(
      diskFixture([
        diskItem("workspace"),
        diskItem("postgres"),
        diskItem("redis"),
      ]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("admin-disk-card")).toHaveLength(3);
    });
    expect(
      document.querySelector('[data-card-name="workspace"]'),
    ).toBeInTheDocument();
    expect(
      document.querySelector('[data-card-name="postgres"]'),
    ).toBeInTheDocument();
  });

  it("colors the progress bar against the threshold (degraded → status=degraded)", async () => {
    mockedGet.mockResolvedValue(
      diskFixture([
        diskItem("workspace", { used_pct: 85, status: "degraded" }),
      ]),
    );
    renderPage();
    await waitFor(() => {
      const card = screen.getByTestId("admin-disk-card");
      expect(card).toHaveAttribute("data-status", "degraded");
    });
    const bar = screen.getByTestId("admin-disk-bar-fill");
    expect(bar).toHaveAttribute("data-used-pct", "85");
    expect(bar).toHaveAttribute("aria-valuenow", "85");
  });

  it("renders a card-level error alert instead of the gauge when telemetry fails", async () => {
    mockedGet.mockResolvedValue(
      diskFixture([
        diskItem("workspace", {
          path: null,
          total_bytes: null,
          used_bytes: 0,
          free_bytes: null,
          used_pct: null,
          status: "down",
          error: "permission denied",
        }),
      ]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-disk-error")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("admin-disk-bar-fill")).not.toBeInTheDocument();
  });

  it("renders skeletons while the query is loading", () => {
    mockedGet.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getAllByTestId("admin-disk-card-skeleton")).toHaveLength(3);
  });

  it("renders the page-level error alert when the query fails", async () => {
    mockedGet.mockRejectedValue(new Error("boom"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-disk-page-error")).toBeInTheDocument();
    });
  });

  it("clamps used_pct above 100 to keep the progress bar within bounds", async () => {
    mockedGet.mockResolvedValue(
      diskFixture([
        diskItem("workspace", { used_pct: 105, status: "down" }),
      ]),
    );
    renderPage();
    await waitFor(() => {
      const bar = screen.getByTestId("admin-disk-bar-fill");
      expect(bar.getAttribute("style") ?? "").toContain("width: 100%");
    });
  });
});
