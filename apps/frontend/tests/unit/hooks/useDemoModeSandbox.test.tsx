/**
 * useDemoMode — sandbox carve-out flag (feat/demo-sandbox-scan).
 *
 * Guards the new `demoSandboxScans` derivation:
 *   - true only when /health reports both demo_read_only + demo_sandbox_scans;
 *   - never true when read-only is off (a normal deploy), even if the flag
 *     somehow appears — writes must never be re-enabled outside the demo.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
}));

import { useDemoMode } from "@/hooks/useDemoMode";
import { api } from "@/lib/api";

const mockedGet = vi.mocked(api.get);

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useDemoMode.demoSandboxScans", () => {
  beforeEach(() => mockedGet.mockReset());

  it("is true when read-only demo + sandbox carve-out are both on", async () => {
    mockedGet.mockResolvedValueOnce({
      data: { status: "ok", demo_read_only: true, demo_sandbox_scans: true },
    });
    const { result } = renderHook(() => useDemoMode(), { wrapper });

    await waitFor(() => expect(result.current.demoSandboxScans).toBe(true));
    expect(result.current.demoReadOnly).toBe(true);
  });

  it("is false in a read-only demo without the carve-out", async () => {
    mockedGet.mockResolvedValueOnce({
      data: { status: "ok", demo_read_only: true, demo_sandbox_scans: false },
    });
    const { result } = renderHook(() => useDemoMode(), { wrapper });

    await waitFor(() => expect(result.current.demoReadOnly).toBe(true));
    expect(result.current.demoSandboxScans).toBe(false);
  });

  it("stays false on a normal deploy even if the flag leaks true", async () => {
    mockedGet.mockResolvedValueOnce({
      data: { status: "ok", demo_read_only: false, demo_sandbox_scans: true },
    });
    const { result } = renderHook(() => useDemoMode(), { wrapper });

    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith("/health"));
    expect(result.current.demoReadOnly).toBe(false);
    expect(result.current.demoSandboxScans).toBe(false);
  });
});
