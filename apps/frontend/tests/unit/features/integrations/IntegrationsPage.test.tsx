/**
 * IntegrationsPage — unit tests for chore C.
 *
 * Covers:
 *   - Renders the API-keys table with rows from the query.
 *   - Empty state when the list is empty.
 *   - Create-key dialog opens, submits, and the reveal dialog shows the
 *     plaintext exactly once.
 *   - Revoke flow (confirmation → mutation called).
 *   - Webhook URLs are rendered with the expected /v1/webhooks/* paths.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ToastProvider } from "@/components/ui/toast";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IntegrationsPage } from "@/features/integrations/IntegrationsPage";
import { useAuthStore, type AuthUser } from "@/stores/authStore";
import type {
  APIKeyCreateOut,
  APIKeyListItem,
  APIKeyListPage,
} from "@/types/apiKey";

vi.mock("@/lib/apiKeysApi", () => ({
  listApiKeys: vi.fn(),
  createApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
  narrowApiKey: vi.fn(),
}));

import {
  createApiKey,
  listApiKeys,
  narrowApiKey,
  revokeApiKey,
} from "@/lib/apiKeysApi";

const mockedList = vi.mocked(listApiKeys);
const mockedCreate = vi.mocked(createApiKey);
const mockedRevoke = vi.mocked(revokeApiKey);
const mockedNarrow = vi.mocked(narrowApiKey);

function key(name: string, overrides: Partial<APIKeyListItem> = {}): APIKeyListItem {
  return {
    id: overrides.id ?? `key-${name}`,
    key_prefix: overrides.key_prefix ?? "tos_a1b2c3d4",
    name,
    scope: overrides.scope ?? "project",
    permission_breadth: overrides.permission_breadth ?? "read_only",
    team_id: overrides.team_id ?? null,
    project_id: overrides.project_id ?? "project-1",
    created_by_user_id: overrides.created_by_user_id ?? "user-1",
    // `created_by_email` may be explicitly null (issuer deleted) — only
    // default it when the caller didn't pass the field at all.
    created_by_email:
      "created_by_email" in overrides
        ? (overrides.created_by_email ?? null)
        : "owner@example.com",
    created_at: overrides.created_at ?? "2026-04-01T00:00:00Z",
    expires_at: overrides.expires_at ?? null,
    last_used_at: overrides.last_used_at ?? null,
    revoked_at: overrides.revoked_at ?? null,
  };
}

function authUser(overrides: Partial<AuthUser> = {}): AuthUser {
  const role = overrides.role ?? "team_admin";
  // /auth/me always carries memberships and `toAuthUser` promotes the global
  // role out of them, so the fixture keeps the two consistent. A super_admin
  // draws its authority from the flag, not from a membership, which is why it
  // sits on the team as a plain developer here.
  const membershipRole = role === "team_admin" ? "team_admin" : "developer";
  return {
    id: "user-1",
    email: "owner@example.com",
    displayName: "Owner",
    role,
    isActive: true,
    isSuperuser: false,
    teamId: "team-1",
    teams: [{ id: "team-1", name: "Platform", role: membershipRole }],
    ...overrides,
  };
}

/** A reader the backend refuses at every scope: no memberships at all. */
function teamlessUser(overrides: Partial<AuthUser> = {}): AuthUser {
  return authUser({
    id: "user-nobody",
    role: "developer",
    teamId: null,
    teams: [],
    ...overrides,
  });
}

/** Put a logged-in user into the store so the role gates (L-16/L-18) open. */
function loginAs(user: AuthUser) {
  useAuthStore.setState({
    user,
    accessToken: "tok",
    status: "authenticated",
    isAuthenticated: true,
  });
}

function page(items: APIKeyListItem[]): APIKeyListPage {
  return { items, total: items.length, page: 1, page_size: 20 };
}

function renderPage(initialEntry = "/integrations") {
  // Fresh QueryClient per test so cached invalidations don't bleed.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <IntegrationsPage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("IntegrationsPage", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedCreate.mockReset();
    mockedRevoke.mockReset();
    // L-18 hides create/revoke from developers — the legacy tests exercise
    // the full management flows, so they run as a team_admin by default.
    loginAs(authUser());
    // jsdom does not ship navigator.clipboard. Define it as a configurable
    // own property so the page's `void copyToClipboard()` calls don't crash.
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it("renders the API-keys table with rows returned by the query", async () => {
    mockedList.mockResolvedValueOnce(
      page([key("ci-runner-prod"), key("ci-runner-staging")]),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getAllByTestId("integrations-key-row")).toHaveLength(2);
    });
    expect(screen.getByText("ci-runner-prod")).toBeInTheDocument();
    expect(screen.getByText("ci-runner-staging")).toBeInTheDocument();
  });

  it("shows the empty state when no keys are returned", async () => {
    mockedList.mockResolvedValueOnce(page([]));

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("integrations-key-row")).not.toBeInTheDocument();
  });

  // C3 - what the card says when there is nothing in it.

  it("claims nothing about keys when the request failed", async () => {
    // Same shape as the scan list: an error and an empty state are different
    // answers, and only one of them was being given.
    mockedList.mockRejectedValue(new Error("boom"));

    renderPage();

    // Same as the scan list: wait for the error to render, or the absence
    // is asserted while the query is still in flight.
    await screen.findByTestId("integrations-keys-error");
    expect(screen.queryByTestId("integrations-keys-empty")).toBeNull();
  });

  it("holds back advice about tokens until there are tokens", async () => {
    // The section description explains how to use these keys with a CI
    // pipeline and warns about revoking an exposed one. Above an empty table
    // it is instructions for something that does not exist, and it was the
    // first thing on the page.
    mockedList.mockResolvedValueOnce(page([]));

    renderPage();

    await screen.findByTestId("integrations-keys-empty");
    expect(screen.queryByTestId("integrations-keys-description")).toBeNull();
  });

  it("shows that advice once a key exists", async () => {
    mockedList.mockResolvedValueOnce(page([key("ci")]));

    renderPage();

    await screen.findByTestId("integrations-key-row");
    expect(
      screen.getByTestId("integrations-keys-description"),
    ).toBeInTheDocument();
  });

  it("offers the key from the empty state, for a reader who can create one", async () => {
    mockedList.mockResolvedValueOnce(page([]));

    renderPage();

    const empty = await screen.findByTestId("integrations-keys-empty");
    expect(empty.textContent).toContain("A build cannot scan without one.");
    // Both hints open with that sentence, so the admin case is only pinned
    // by the half that differs.
    expect(empty.textContent).not.toContain("Ask a team administrator");

    // Pressed, not just present: a button wired to nothing would have passed.
    await userEvent.click(screen.getByTestId("integrations-keys-empty-create"));
    expect(
      await screen.findByTestId("integrations-create-dialog"),
    ).toBeInTheDocument();
  });

  it("tells a reader with no team who to ask instead of what to press", async () => {
    // The old copy - "No API keys yet. Create one to get started." - pointed
    // at a button that is not rendered. It is only absent for a reader with
    // no memberships now (#136); a developer on a team gets the button.
    loginAs(teamlessUser());
    mockedList.mockResolvedValueOnce(page([]));

    renderPage();

    const empty = await screen.findByTestId("integrations-keys-empty");
    expect(empty.textContent).toContain("you belong to no team yet");
    expect(screen.queryByTestId("integrations-keys-empty-create")).toBeNull();
  });

  it("offers the key from the empty state to a developer on a team (#136)", async () => {
    loginAs(authUser({ id: "user-dev", role: "developer" }));
    mockedList.mockResolvedValueOnce(page([]));

    renderPage();

    const empty = await screen.findByTestId("integrations-keys-empty");
    expect(empty.textContent).not.toContain("you belong to no team yet");

    await userEvent.click(screen.getByTestId("integrations-keys-empty-create"));
    expect(
      await screen.findByTestId("integrations-create-dialog"),
    ).toBeInTheDocument();
  });

  it("opens the create dialog and reveals the raw key on success", async () => {
    mockedList.mockResolvedValue(page([]));
    const created: APIKeyCreateOut = {
      id: "k-99",
      key_prefix: "tos_99887766",
      name: "release-bot",
      scope: "project",      permission_breadth: "read_only",

      team_id: null,
      project_id: "p-1",
      created_by_user_id: "u-1",
      created_at: "2026-05-09T10:00:00Z",
      expires_at: null,
      raw_key: "tos_99887766_super-secret-payload-xyz",
    };
    mockedCreate.mockResolvedValueOnce(created);

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("integrations-create-key"));
    expect(
      await screen.findByTestId("integrations-create-dialog"),
    ).toBeInTheDocument();

    await user.type(
      screen.getByTestId("integrations-create-name"),
      "release-bot",
    );
    // Default scope is "project" — supply a project id.
    await user.type(
      screen.getByTestId("integrations-create-project-id"),
      "p-1",
    );
    await user.click(screen.getByTestId("integrations-create-submit"));

    // The reveal dialog must show the plaintext exactly once, with a copy
    // button. Critical security boundary — the key is never echoed back
    // by the list endpoint, so this dialog is the user's only chance.
    const revealValue = await screen.findByTestId(
      "integrations-reveal-key-value",
    );
    expect(revealValue).toHaveTextContent(created.raw_key);
    expect(
      screen.getByTestId("integrations-reveal-copy"),
    ).toBeInTheDocument();
    expect(mockedCreate).toHaveBeenCalledWith({
      name: "release-bot",
      scope: "project",      permission_breadth: "read_only",

      team_id: null,
      project_id: "p-1",
      // No expiry preset chosen → the key never expires.
      expires_in_days: null,
    });
  });

  it("sends the chosen expiry preset as expires_in_days", async () => {
    mockedList.mockResolvedValue(page([]));
    const created: APIKeyCreateOut = {
      id: "k-exp",
      key_prefix: "tos_exp",
      name: "ttl-bot",
      scope: "project",      permission_breadth: "read_only",

      team_id: null,
      project_id: "p-1",
      created_by_user_id: "u-1",
      created_at: "2026-05-09T10:00:00Z",
      expires_at: "2026-08-07T10:00:00Z",
      raw_key: "tos_exp_secret",
    };
    mockedCreate.mockResolvedValueOnce(created);

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("integrations-create-key"));
    await screen.findByTestId("integrations-create-dialog");
    await user.type(screen.getByTestId("integrations-create-name"), "ttl-bot");
    await user.type(
      screen.getByTestId("integrations-create-project-id"),
      "p-1",
    );
    await user.selectOptions(
      screen.getByTestId("integrations-create-expires"),
      "90",
    );
    await user.click(screen.getByTestId("integrations-create-submit"));

    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    expect(mockedCreate).toHaveBeenCalledWith({
      name: "ttl-bot",
      scope: "project",      permission_breadth: "read_only",

      team_id: null,
      project_id: "p-1",
      expires_in_days: 90,
    });
  });

  it("blocks create submit when name is empty (no network)", async () => {
    mockedList.mockResolvedValue(page([]));
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("integrations-create-key"));
    await user.click(screen.getByTestId("integrations-create-submit"));

    expect(
      await screen.findByTestId("integrations-create-error"),
    ).toBeInTheDocument();
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it("revokes a key after confirmation", async () => {
    const k = key("doomed");
    mockedList.mockResolvedValue(page([k]));
    mockedRevoke.mockResolvedValueOnce(undefined);

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("integrations-key-row")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("integrations-key-revoke"));
    expect(
      await screen.findByTestId("integrations-revoke-dialog"),
    ).toBeInTheDocument();
    await user.click(screen.getByTestId("integrations-revoke-confirm"));

    await waitFor(() => {
      expect(mockedRevoke).toHaveBeenCalledWith(k.id);
    });
  });

  it("renders the webhook URL panels with the expected backend paths", async () => {
    mockedList.mockResolvedValue(page([]));
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });

    const github = screen.getByTestId("integrations-webhook-github-url");
    const gitlab = screen.getByTestId("integrations-webhook-gitlab-url");
    expect(github.textContent).toMatch(/\/v1\/webhooks\/github$/);
    expect(gitlab.textContent).toMatch(/\/v1\/webhooks\/gitlab$/);
  });

  it("shows the error alert when the list query fails", async () => {
    mockedList.mockRejectedValueOnce(new Error("boom"));
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-error")).toBeInTheDocument();
    });
  });

  it("creates a team-scoped key when the user picks scope=team", async () => {
    mockedList.mockResolvedValue(page([]));
    const created: APIKeyCreateOut = {
      id: "k-team",
      key_prefix: "tos_teamteam",
      name: "team-runner",
      scope: "team",      permission_breadth: "read_only",

      team_id: "t-1",
      project_id: null,
      created_by_user_id: "u-1",
      created_at: "2026-05-09T11:00:00Z",
      expires_at: null,
      raw_key: "tos_teamteam_secret-payload",
    };
    mockedCreate.mockResolvedValueOnce(created);

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("integrations-create-key"));
    await screen.findByTestId("integrations-create-dialog");

    await user.type(
      screen.getByTestId("integrations-create-name"),
      "team-runner",
    );
    await user.selectOptions(
      screen.getByTestId("integrations-create-scope"),
      "team",
    );
    await user.type(
      screen.getByTestId("integrations-create-team-id"),
      "t-1",
    );
    await user.click(screen.getByTestId("integrations-create-submit"));

    await waitFor(() => {
      expect(mockedCreate).toHaveBeenCalledWith({
        name: "team-runner",
        scope: "team",        permission_breadth: "read_only",

        team_id: "t-1",
        project_id: null,
        expires_in_days: null,
      });
    });
  });

  it("blocks team-scoped create when team_id is empty", async () => {
    mockedList.mockResolvedValue(page([]));
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("integrations-create-key"));
    await user.type(
      screen.getByTestId("integrations-create-name"),
      "needs-team",
    );
    await user.selectOptions(
      screen.getByTestId("integrations-create-scope"),
      "team",
    );
    await user.click(screen.getByTestId("integrations-create-submit"));

    expect(
      await screen.findByTestId("integrations-create-error"),
    ).toBeInTheDocument();
    expect(mockedCreate).not.toHaveBeenCalled();
  });

  it("dismisses the create dialog when Cancel is clicked", async () => {
    mockedList.mockResolvedValue(page([]));
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("integrations-create-key"));
    await screen.findByTestId("integrations-create-dialog");
    await user.click(screen.getByTestId("integrations-create-cancel"));

    await waitFor(() => {
      expect(
        screen.queryByTestId("integrations-create-dialog"),
      ).not.toBeInTheDocument();
    });
  });

  it("dismisses the revoke dialog when Cancel is clicked", async () => {
    const k = key("safe");
    mockedList.mockResolvedValue(page([k]));
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("integrations-key-row")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("integrations-key-revoke"));
    await screen.findByTestId("integrations-revoke-dialog");
    await user.click(screen.getByTestId("integrations-revoke-cancel"));

    await waitFor(() => {
      expect(
        screen.queryByTestId("integrations-revoke-dialog"),
      ).not.toBeInTheDocument();
    });
    expect(mockedRevoke).not.toHaveBeenCalled();
  });

  it("surfaces a toast when the create mutation fails", async () => {
    mockedList.mockResolvedValue(page([]));
    mockedCreate.mockRejectedValueOnce(new Error("boom"));

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("integrations-create-key"));
    await user.type(
      screen.getByTestId("integrations-create-name"),
      "boom-key",
    );
    await user.type(
      screen.getByTestId("integrations-create-project-id"),
      "p-1",
    );
    await user.click(screen.getByTestId("integrations-create-submit"));

    await waitFor(() => {
      const toast = screen.getByTestId("admin-toast");
      expect(toast).toHaveAttribute("data-tone", "error");
      expect(toast).toHaveAttribute("data-toast-key", "create_failed");
    });
  });

  it("surfaces a toast when the revoke mutation fails", async () => {
    const k = key("doomed-2");
    mockedList.mockResolvedValue(page([k]));
    mockedRevoke.mockRejectedValueOnce(new Error("nope"));

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-key-row")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("integrations-key-revoke"));
    await screen.findByTestId("integrations-revoke-dialog");
    await user.click(screen.getByTestId("integrations-revoke-confirm"));

    await waitFor(() => {
      const toast = screen.getByTestId("admin-toast");
      expect(toast).toHaveAttribute("data-tone", "error");
      expect(toast).toHaveAttribute("data-toast-key", "revoke_failed");
    });
  });

  it("renders pagination controls when total > page_size", async () => {
    // 25 rows ≥ 21 forces a second page (page_size = 20).
    const rows = Array.from({ length: 20 }).map((_, i) =>
      key(`k-${i}`, { id: `id-${i}` }),
    );
    mockedList.mockResolvedValue({
      items: rows,
      total: 25,
      page: 1,
      page_size: 20,
    });

    const user = userEvent.setup();
    renderPage();
    const pager = await screen.findByTestId("integrations-pagination");
    expect(pager).toBeInTheDocument();

    await user.click(screen.getByTestId("integrations-page-next"));
    // After click the query refires; the second call reflects page=2.
    await waitFor(() => {
      const lastCall = mockedList.mock.calls.at(-1)?.[0];
      expect(lastCall?.page).toBe(2);
    });
  });

  it("opens on the page the URL asked for (B1)", async () => {
    mockedList.mockResolvedValue({ items: [], total: 25, page: 2, page_size: 20 });

    renderPage("/integrations?page=2");

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    expect(mockedList.mock.calls[0]?.[0]?.page).toBe(2);
    // And it stays there: nothing snaps a page the list actually has.
    expect(mockedList.mock.calls.at(-1)?.[0]?.page).toBe(2);
  });

  it("snaps a page the list does not have back into range (B1)", async () => {
    // A bookmark can name page 2 of a list whose keys have since been
    // revoked down to one page. Without this the footer lies about the range.
    mockedList.mockResolvedValue({ items: [], total: 3, page: 1, page_size: 20 });

    renderPage("/integrations?page=2");

    await waitFor(() => {
      expect(mockedList.mock.calls.at(-1)?.[0]?.page).toBe(1);
    });
  });

  it("renders rendered key as revoked (no Revoke button) when revoked_at is set", async () => {
    mockedList.mockResolvedValue(
      page([
        key("dead", { revoked_at: "2026-05-08T00:00:00Z" }),
        key("alive"),
      ]),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getAllByTestId("integrations-key-row")).toHaveLength(2);
    });
    // Only the live key has a revoke button.
    expect(screen.getAllByTestId("integrations-key-revoke")).toHaveLength(1);
  });

  // -------------------------------------------------------------------------
  // L-16 — create-dialog scope options follow the caller's role
  // -------------------------------------------------------------------------

  it("hides the org scope option from team_admin (L-16)", async () => {
    loginAs(authUser({ role: "team_admin", isSuperuser: false }));
    mockedList.mockResolvedValue(page([]));

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("integrations-create-key"));
    const select = await screen.findByTestId("integrations-create-scope");
    const values = within(select)
      .getAllByRole("option")
      .map((o) => (o as HTMLOptionElement).value);
    expect(values).toEqual(["project", "team"]);
    // The default selection must never be a hidden option.
    expect((select as HTMLSelectElement).value).toBe("project");
  });

  it("offers all three scope options to super_admin (L-16)", async () => {
    loginAs(authUser({ role: "super_admin", isSuperuser: true }));
    mockedList.mockResolvedValue(page([]));

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("integrations-create-key"));
    const select = await screen.findByTestId("integrations-create-scope");
    const values = within(select)
      .getAllByRole("option")
      .map((o) => (o as HTMLOptionElement).value);
    expect(values).toEqual(["project", "team", "org"]);
  });

  it("offers a developer the project scope only (#136)", async () => {
    // Backend rule (`_can_issue_at_scope`): any team member may issue a
    // project-scoped key, team scope needs team_admin OF that team, org
    // scope needs super_admin. Offering team here would be a certain 403.
    loginAs(authUser({ id: "user-dev", role: "developer" }));
    mockedList.mockResolvedValue(page([]));

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("integrations-create-key"));
    const select = await screen.findByTestId("integrations-create-scope");
    const values = within(select)
      .getAllByRole("option")
      .map((o) => (o as HTMLOptionElement).value);
    expect(values).toEqual(["project"]);
    expect((select as HTMLSelectElement).value).toBe("project");
  });

  it("lets a developer issue the project-scoped key the backend grants (#136)", async () => {
    loginAs(authUser({ id: "user-dev", role: "developer" }));
    mockedList.mockResolvedValue(page([]));
    const created: APIKeyCreateOut = {
      id: "k-dev",
      key_prefix: "tos_devdevde",
      name: "dev-runner",
      scope: "project",      permission_breadth: "read_only",

      team_id: "team-1",
      project_id: "p-7",
      created_by_user_id: "user-dev",
      created_at: "2026-08-17T09:00:00Z",
      expires_at: null,
      raw_key: "tos_devdevde_secret-payload",
    };
    mockedCreate.mockResolvedValueOnce(created);

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("integrations-create-key"));
    await screen.findByTestId("integrations-create-dialog");
    await user.type(
      screen.getByTestId("integrations-create-name"),
      "dev-runner",
    );
    await user.type(
      screen.getByTestId("integrations-create-project-id"),
      "p-7",
    );
    await user.click(screen.getByTestId("integrations-create-submit"));

    await waitFor(() => {
      expect(mockedCreate).toHaveBeenCalledWith({
        name: "dev-runner",
        scope: "project",        permission_breadth: "read_only",

        team_id: null,
        project_id: "p-7",
        expires_in_days: null,
      });
    });
  });

  // -------------------------------------------------------------------------
  // L-17 — creator / last-used / status columns
  // -------------------------------------------------------------------------

  it("renders creator, last-used and status columns (L-17)", async () => {
    mockedList.mockResolvedValue(
      page([
        key("alive", {
          created_by_email: "alice@example.com",
          last_used_at: "2026-06-09T00:00:00Z",
        }),
        key("dead", {
          created_by_email: null,
          last_used_at: null,
          revoked_at: "2026-05-08T00:00:00Z",
        }),
      ]),
    );

    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("integrations-key-row")).toHaveLength(2);
    });

    const [aliveRow, deadRow] = screen.getAllByTestId("integrations-key-row");

    // Creator column: email, or an em-dash when the issuer was deleted.
    expect(
      within(aliveRow).getByTestId("integrations-key-creator"),
    ).toHaveTextContent("alice@example.com");
    expect(
      within(deadRow).getByTestId("integrations-key-creator"),
    ).toHaveTextContent("—");

    // Last-used column: relative time vs. the i18n "never used" copy.
    expect(
      within(aliveRow).getByTestId("integrations-key-last-used").textContent,
    ).not.toMatch(/never/i);
    expect(
      within(deadRow).getByTestId("integrations-key-last-used"),
    ).toHaveTextContent(/never used/i);

    // Status column: badge pairs the tone with a visible label (a11y).
    const aliveStatus = within(aliveRow).getByTestId("integrations-key-status");
    expect(aliveStatus).toHaveAttribute("data-status", "active");
    expect(aliveStatus).toHaveTextContent(/active/i);
    const deadStatus = within(deadRow).getByTestId("integrations-key-status");
    expect(deadStatus).toHaveAttribute("data-status", "revoked");
    expect(deadStatus).toHaveTextContent(/revoked/i);
  });

  // -------------------------------------------------------------------------
  // L-18 — developer action gating
  // -------------------------------------------------------------------------

  it("hides the create button from a reader who belongs to no team (#136)", async () => {
    loginAs(teamlessUser());
    mockedList.mockResolvedValue(page([]));

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("integrations-create-key"),
    ).not.toBeInTheDocument();
  });

  it("shows the create button to a developer on a team (#136)", async () => {
    loginAs(authUser({ id: "user-dev", role: "developer" }));
    mockedList.mockResolvedValue(page([]));

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-keys-empty")).toBeInTheDocument();
    });
    expect(screen.getByTestId("integrations-create-key")).toBeInTheDocument();
  });

  it("shows revoke to developers only on keys they issued (L-18)", async () => {
    loginAs(authUser({ id: "user-dev", role: "developer" }));
    mockedList.mockResolvedValue(
      page([
        key("mine", { created_by_user_id: "user-dev" }),
        key("someone-elses", { created_by_user_id: "user-other" }),
      ]),
    );

    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("integrations-key-row")).toHaveLength(2);
    });

    // Exactly one revoke button, and it lives on the self-issued row.
    const buttons = screen.getAllByTestId("integrations-key-revoke");
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAttribute("data-key-id", "key-mine");
  });

  it("shows revoke to team_admin on rows issued by others (L-18)", async () => {
    loginAs(authUser({ id: "user-admin", role: "team_admin" }));
    mockedList.mockResolvedValue(
      page([key("someone-elses", { created_by_user_id: "user-other" })]),
    );

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("integrations-key-row")).toBeInTheDocument();
    });
    expect(screen.getByTestId("integrations-key-revoke")).toBeInTheDocument();
  });

  it("issues a read-only key unless the form says otherwise", async () => {
    // The default that makes the setting worth having. Most keys go to
    // something that reads results, and the ones that start scans should be
    // a deliberate choice rather than what you get by not looking.
    mockedList.mockResolvedValue(page([]));
    mockedCreate.mockResolvedValue({
      id: "k1",
      // Short and non-random on purpose: the secret scanner reads a
      // high-entropy string after a key-shaped name as a credential, and the
      // other fixtures in this file keep the same shape for that reason.
      key_prefix: "tos_ci",
      name: "ci",
      scope: "project",
      permission_breadth: "read_only",
      team_id: null,
      project_id: "p-1",
      created_by_user_id: "u1",
      created_at: "2026-08-18T00:00:00Z",
      expires_at: null,
      raw_key: "tos_ci_secret",
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByTestId("integrations-create-key"));
    await user.type(screen.getByTestId("integrations-create-name"), "ci");
    await user.type(screen.getByTestId("integrations-create-project-id"), "p-1");
    await user.click(screen.getByTestId("integrations-create-submit"));

    await waitFor(() => expect(mockedCreate).toHaveBeenCalled());
    expect(mockedCreate.mock.calls[0][0].permission_breadth).toBe("read_only");
  });

  it("says which keys can change something", async () => {
    // The column somebody scans when they are deciding what to revoke. Read
    // and write is the notable state, so it is the one that carries a tone.
    mockedList.mockResolvedValue(
      page([
        key("reader", { permission_breadth: "read_only" }),
        key("writer", { permission_breadth: "read_write" }),
      ]),
    );
    renderPage();

    const badges = await screen.findAllByTestId("integrations-breadth-badge");
    expect(badges.map((b) => b.getAttribute("data-breadth"))).toEqual([
      "read_only",
      "read_write",
    ]);
  });


  it("offers to narrow a read-write key, and not a read-only one", async () => {
    // The guide tells operators to use the breadth column to find keys worth
    // narrowing, so the control has to be where they are told to look. It
    // shipped without one once.
    mockedList.mockResolvedValue(
      page([
        key("writer", { permission_breadth: "read_write" }),
        key("reader", { permission_breadth: "read_only" }),
      ]),
    );
    renderPage();

    const buttons = await screen.findAllByTestId("integrations-key-narrow");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].getAttribute("data-key-id")).toBe("key-writer");
  });

  it("narrows the key it was asked to narrow", async () => {
    const user = userEvent.setup();
    mockedList.mockResolvedValue(
      page([key("writer", { permission_breadth: "read_write" })]),
    );
    mockedNarrow.mockResolvedValue(
      key("writer", { permission_breadth: "read_only" }),
    );
    renderPage();

    await user.click(await screen.findByTestId("integrations-key-narrow"));

    await waitFor(() => expect(mockedNarrow).toHaveBeenCalledWith("key-writer"));
  });

  it("puts the breadth back to read-only when the dialog is reopened", async () => {
    // Every other field resets, so a form that looks blank teaches people not
    // to re-read it. The one field worth re-reading is this one.
    const user = userEvent.setup();
    mockedList.mockResolvedValue(page([]));
    renderPage();

    await user.click(await screen.findByTestId("integrations-create-key"));
    await user.selectOptions(
      screen.getByTestId("integrations-create-breadth"),
      "read_write",
    );
    await user.click(screen.getByTestId("integrations-create-cancel"));
    await user.click(await screen.findByTestId("integrations-create-key"));

    expect(screen.getByTestId("integrations-create-breadth")).toHaveValue(
      "read_only",
    );
  });

});
