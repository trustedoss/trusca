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
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IntegrationsPage } from "@/features/integrations/IntegrationsPage";
import type {
  APIKeyCreateOut,
  APIKeyListItem,
  APIKeyListPage,
} from "@/types/apiKey";

vi.mock("@/lib/apiKeysApi", () => ({
  listApiKeys: vi.fn(),
  createApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
}));

import {
  createApiKey,
  listApiKeys,
  revokeApiKey,
} from "@/lib/apiKeysApi";

const mockedList = vi.mocked(listApiKeys);
const mockedCreate = vi.mocked(createApiKey);
const mockedRevoke = vi.mocked(revokeApiKey);

function key(name: string, overrides: Partial<APIKeyListItem> = {}): APIKeyListItem {
  return {
    id: overrides.id ?? `key-${name}`,
    key_prefix: overrides.key_prefix ?? "tos_a1b2c3d4",
    name,
    scope: overrides.scope ?? "project",
    team_id: overrides.team_id ?? null,
    project_id: overrides.project_id ?? "project-1",
    created_by_user_id: overrides.created_by_user_id ?? "user-1",
    created_at: overrides.created_at ?? "2026-04-01T00:00:00Z",
    expires_at: overrides.expires_at ?? null,
    last_used_at: overrides.last_used_at ?? null,
    revoked_at: overrides.revoked_at ?? null,
  };
}

function page(items: APIKeyListItem[]): APIKeyListPage {
  return { items, total: items.length, page: 1, page_size: 20 };
}

function renderPage() {
  // Fresh QueryClient per test so cached invalidations don't bleed.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <IntegrationsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("IntegrationsPage", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedCreate.mockReset();
    mockedRevoke.mockReset();
    // jsdom does not ship navigator.clipboard. Define it as a configurable
    // own property so the page's `void copyToClipboard()` calls don't crash.
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
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

  it("opens the create dialog and reveals the raw key on success", async () => {
    mockedList.mockResolvedValue(page([]));
    const created: APIKeyCreateOut = {
      id: "k-99",
      key_prefix: "tos_99887766",
      name: "release-bot",
      scope: "project",
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
      scope: "project",
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
      scope: "project",
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
      scope: "project",
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
      scope: "team",
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
        scope: "team",
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
});
