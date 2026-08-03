/**
 * DemoBanner + useDemoMode — unit tests for v2.1 Track B (B5).
 *
 * Coverage:
 *   - The banner is HIDDEN when the backend reports demo_read_only = false.
 *   - The banner is SHOWN (with the i18n title) when the backend reports
 *     demo_read_only = true.
 *   - The banner is hidden when /health omits the flag (defensive default).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DemoBanner } from "@/components/DemoBanner";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
}));

import { api } from "@/lib/api";

const mockedGet = vi.mocked(api.get);

function renderBanner() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <DemoBanner />
    </QueryClientProvider>,
  );
}

describe("DemoBanner", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("hides the banner when the backend is not in read-only demo mode", async () => {
    mockedGet.mockResolvedValueOnce({
      data: { status: "ok", demo_read_only: false },
    });
    renderBanner();

    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith("/health");
    });
    expect(screen.queryByTestId("demo-banner")).not.toBeInTheDocument();
  });

  it("shows the banner when the backend reports demo_read_only = true", async () => {
    mockedGet.mockResolvedValueOnce({
      data: { status: "ok", demo_read_only: true },
    });
    renderBanner();

    await waitFor(() => {
      expect(screen.getByTestId("demo-banner")).toBeInTheDocument();
    });
    // The EN i18n title renders.
    expect(screen.getByTestId("demo-banner")).toHaveTextContent("Read-only demo");
  });

  it("hides the banner when /health omits the flag (defensive default)", async () => {
    mockedGet.mockResolvedValueOnce({ data: { status: "ok" } });
    renderBanner();

    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith("/health");
    });
    expect(screen.queryByTestId("demo-banner")).not.toBeInTheDocument();
  });
});
