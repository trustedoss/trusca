/**
 * The panel that makes a credential outlive a person.
 *
 * The state worth rendering is "no steward": existing keys still work, and no
 * new one may be issued until somebody takes the account over. If that were
 * invisible, the account would sit there refusing key issuance with nothing on
 * screen explaining why.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ServiceAccountsPanel } from "@/features/integrations/ServiceAccountsPanel";

const listServiceAccounts = vi.fn();
const createServiceAccount = vi.fn();
const deactivateServiceAccount = vi.fn();
const assignServiceAccountSteward = vi.fn();

vi.mock("@/lib/serviceAccountsApi", () => ({
  listServiceAccounts: (...args: unknown[]) => listServiceAccounts(...args),
  createServiceAccount: (...args: unknown[]) => createServiceAccount(...args),
  deactivateServiceAccount: (...args: unknown[]) =>
    deactivateServiceAccount(...args),
  assignServiceAccountSteward: (...args: unknown[]) =>
    assignServiceAccountSteward(...args),
}));

vi.mock("@/stores/authStore", () => ({
  useAuthStore: (selector: (s: unknown) => unknown) =>
    selector({ user: { id: "me" } }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

function account(overrides: Record<string, unknown> = {}) {
  return {
    id: "sa-1",
    email: "nightly@svc.trusca.internal",
    full_name: "Nightly build",
    is_active: true,
    managed_by_user_id: "u-1",
    created_at: "2026-08-19T00:00:00Z",
    ...overrides,
  };
}

function renderPanel(canManage = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ServiceAccountsPanel
        teamId="team-1"
        canManage={canManage}
        onNotify={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

describe("ServiceAccountsPanel", () => {
  beforeEach(() => {
    listServiceAccounts.mockReset();
    createServiceAccount.mockReset();
    deactivateServiceAccount.mockReset();
    assignServiceAccountSteward.mockReset();
  });

  it("marks an account nobody is answerable for", async () => {
    listServiceAccounts.mockResolvedValue({
      items: [account({ managed_by_user_id: null })],
      total: 1,
    });

    renderPanel();

    expect(
      await screen.findByTestId("service-account-unowned"),
    ).toBeInTheDocument();
  });

  it("says nothing extra about an account that has a steward", async () => {
    // A badge on every row would be noise, and the one state worth spotting
    // would stop standing out.
    listServiceAccounts.mockResolvedValue({ items: [account()], total: 1 });

    renderPanel();

    await screen.findByTestId("service-account-sa-1");
    expect(screen.queryByTestId("service-account-unowned")).not.toBeInTheDocument();
  });

  it("offers to stop the keys of an active account", async () => {
    listServiceAccounts.mockResolvedValue({ items: [account()], total: 1 });
    deactivateServiceAccount.mockResolvedValue(account({ is_active: false }));
    renderPanel();

    await userEvent.click(
      await screen.findByTestId("service-account-deactivate-sa-1"),
    );

    await waitFor(() =>
      expect(deactivateServiceAccount).toHaveBeenCalledWith("sa-1"),
    );
  });

  it("does not offer to stop one that is already stopped", async () => {
    listServiceAccounts.mockResolvedValue({
      items: [account({ is_active: false })],
      total: 1,
    });

    renderPanel();

    await screen.findByTestId("service-account-stopped");
    expect(
      screen.queryByTestId("service-account-deactivate-sa-1"),
    ).not.toBeInTheDocument();
  });

  it("hides the create form from somebody who may not issue credentials", async () => {
    listServiceAccounts.mockResolvedValue({ items: [], total: 0 });

    renderPanel(false);

    await screen.findByTestId("service-accounts-empty");
    expect(
      screen.queryByTestId("service-accounts-create-form"),
    ).not.toBeInTheDocument();
  });

  it("creates one from the name typed in", async () => {
    listServiceAccounts.mockResolvedValue({ items: [], total: 0 });
    createServiceAccount.mockResolvedValue(account());
    renderPanel();

    await userEvent.type(
      await screen.findByTestId("service-account-slug"),
      "nightly-build",
    );
    await userEvent.click(screen.getByTestId("service-account-create"));

    await waitFor(() => expect(createServiceAccount).toHaveBeenCalled());
    expect(createServiceAccount.mock.calls[0][0]).toMatchObject({
      team_id: "team-1",
      slug: "nightly-build",
    });
  });

  it("offers to take over an account nobody is answerable for", async () => {
    // The recovery the guide promises. Without it the panel says "no steward",
    // the server refuses new keys, and the ways out are re-activating the
    // person who left or leaving live credentials unowned.
    listServiceAccounts.mockResolvedValue({
      items: [account({ managed_by_user_id: null })],
      total: 1,
    });
    assignServiceAccountSteward.mockResolvedValue(account());
    renderPanel();

    await userEvent.click(
      await screen.findByTestId("service-account-take-over-sa-1"),
    );

    await waitFor(() =>
      expect(assignServiceAccountSteward).toHaveBeenCalledWith("sa-1", "me"),
    );
  });

  it("does not offer to take over one that already has a steward", async () => {
    listServiceAccounts.mockResolvedValue({ items: [account()], total: 1 });

    renderPanel();

    await screen.findByTestId("service-account-sa-1");
    expect(
      screen.queryByTestId("service-account-take-over-sa-1"),
    ).not.toBeInTheDocument();
  });

});
