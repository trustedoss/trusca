/**
 * The gateway's brand panel (W16-c).
 *
 * The panel exists to say what the product is before someone signs in, and
 * the only thing worth pinning about it is that it says checkable things.
 * A pitch is a matter of taste; "Apache-2.0", "NVD / OSV / GHSA", "CycloneDX
 * / SPDX" are claims the product either honours or does not, and if one of
 * them stops being true this test is where it should hurt.
 *
 * The layout itself is not asserted here. Whether the panel sits beside the
 * form or above it is a media query, and jsdom has no viewport — the visual
 * baseline and the narrow-viewport gate cover that, and they cover it with
 * real pixels rather than a class-name assertion pretending to.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthLayout } from "@/pages/auth/AuthLayout";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn().mockResolvedValue({ data: {} }) } }));

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

describe("AuthLayout gateway panel", () => {
  it("states what the product runs on, reads from, and exports", () => {
    renderLayout();

    const panel = screen.getByTestId("auth-brand-panel");
    expect(panel.textContent).toContain("Apache-2.0");
    expect(panel.textContent).toContain("NVD");
    expect(panel.textContent).toContain("CycloneDX");
    expect(panel.textContent).toContain("SPDX");
  });

  it("does not push the form off the page it exists to serve", () => {
    renderLayout();

    // The panel is decoration around a task. If the form ever stops rendering
    // beside it, the gateway has become a landing page.
    expect(screen.getByTestId("auth-children")).toBeInTheDocument();
    expect(screen.getByTestId("login-page")).toBeInTheDocument();
  });
});
