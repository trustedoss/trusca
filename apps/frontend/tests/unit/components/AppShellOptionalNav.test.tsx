/**
 * Optional surfaces do not get a menu row.
 *
 * A row for a feature the deployment has not turned on links to a page that
 * says the deployment does not use it. That is not a permission problem and
 * should not read as one: the row would teach people the product is broken,
 * and it would do so on the sidebar, which is where they learn what the
 * product is.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/AppShell";

const useDeploymentFeatures = vi.fn();

vi.mock("@/features/about/api/useDeploymentFeatures", () => ({
  useDeploymentFeatures: () => useDeploymentFeatures(),
}));

vi.mock("@/features/notifications/useNotifications", () => ({
  useUnreadCount: () => ({ data: undefined }),
}));

vi.mock("@/hooks/useNavBadges", () => ({
  useNavBadges: () => ({}),
}));

function renderShell() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects"]}>
        <AppShell />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AppShell optional surfaces", () => {
  beforeEach(() => {
    useDeploymentFeatures.mockReset();
  });

  it("draws no intake row when the deployment has not turned it on", async () => {
    useDeploymentFeatures.mockReturnValue({});

    renderShell();

    await waitFor(() => expect(screen.getAllByTestId("nav-scans").length).toBeGreaterThan(0));
    expect(screen.queryByTestId("nav-intake")).toBeNull();
  });

  it("draws it when the deployment has", async () => {
    useDeploymentFeatures.mockReturnValue({ intake_requests: true });

    renderShell();

    await waitFor(() =>
      expect(screen.getAllByTestId("nav-intake").length).toBeGreaterThan(0),
    );
  });

  it("keeps the rows that are not optional either way", async () => {
    // The filter must narrow only what it was given a key for. An earlier
    // version that dropped rows without a feature key would have emptied the
    // sidebar on every deployment.
    useDeploymentFeatures.mockReturnValue({});

    renderShell();

    await waitFor(() => expect(screen.getAllByTestId("nav-scans").length).toBeGreaterThan(0));
    expect(screen.getAllByTestId("nav-approvals").length).toBeGreaterThan(0);
    expect(screen.getAllByTestId("nav-policies").length).toBeGreaterThan(0);
  });
});
