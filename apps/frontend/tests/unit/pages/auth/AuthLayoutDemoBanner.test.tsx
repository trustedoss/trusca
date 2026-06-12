/**
 * AuthLayout demo banner — v2.1 Track B (B5).
 *
 * Before B5 the read-only-demo banner only showed inside the authenticated
 * shell. AuthLayout now renders the same `DemoBanner` so an UNauthenticated
 * visitor on the login / register screens also sees that writes are disabled.
 * It must stay hidden on a normal (non-demo) deploy.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthLayout } from "@/pages/auth/AuthLayout";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
}));

import { api } from "@/lib/api";

const mockedGet = vi.mocked(api.get);

function renderLayout() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AuthLayout title="Sign in" testId="login-page">
        <div data-testid="auth-children" />
      </AuthLayout>
    </QueryClientProvider>,
  );
}

describe("AuthLayout demo banner", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("shows the demo banner to unauthenticated visitors in read-only demo mode", async () => {
    mockedGet.mockResolvedValueOnce({
      data: { status: "ok", demo_read_only: true },
    });
    renderLayout();

    expect(await screen.findByTestId("demo-banner")).toBeInTheDocument();
    expect(screen.getByTestId("demo-banner")).toHaveTextContent("Read-only demo");
  });

  it("hides the demo banner on a normal (non-demo) deploy", async () => {
    mockedGet.mockResolvedValueOnce({
      data: { status: "ok", demo_read_only: false },
    });
    renderLayout();

    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith("/health");
    });
    expect(screen.queryByTestId("demo-banner")).not.toBeInTheDocument();
  });
});
