/**
 * UserProfilePage — unit tests for chore G ("Connected Accounts" UI).
 *
 * Covers:
 *   - Renders both connected identities with Unlink buttons.
 *   - Click Unlink → inline confirmation strip → confirm → API + toast +
 *     row removed (cache invalidation refetches an empty list).
 *   - 409 response with the urn:trustedoss:problem:oauth_unlink_blocks_login
 *     type → inline blocks-login alert visible, row stays, no toast.
 *   - Empty state when API returns `{ items: [] }`.
 *   - Generic error → toast surfaces, row stays.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UserProfilePage } from "@/features/profile/UserProfilePage";
import {
  OAUTH_UNLINK_BLOCKS_LOGIN_TYPE,
  type OAuthIdentity,
} from "@/features/profile/api/oauthIdentitiesApi";
import { ProblemError } from "@/lib/problem";
import { useAuthStore } from "@/stores/authStore";

vi.mock("@/features/profile/api/oauthIdentitiesApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/profile/api/oauthIdentitiesApi")
  >("@/features/profile/api/oauthIdentitiesApi");
  return {
    ...actual,
    listIdentities: vi.fn(),
    unlinkIdentity: vi.fn(),
  };
});

import {
  listIdentities,
  unlinkIdentity,
} from "@/features/profile/api/oauthIdentitiesApi";

const mockedList = vi.mocked(listIdentities);
const mockedUnlink = vi.mocked(unlinkIdentity);

function identity(
  overrides: Partial<OAuthIdentity> = {},
): OAuthIdentity {
  return {
    id: overrides.id ?? "id-github-1",
    provider: overrides.provider ?? "github",
    provider_user_id: overrides.provider_user_id ?? "12345",
    provider_email: overrides.provider_email ?? "alice@example.com",
    created_at: overrides.created_at ?? "2026-04-01T00:00:00Z",
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <UserProfilePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("UserProfilePage — Connected Accounts", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedUnlink.mockReset();
    // Seed the auth store so the header section renders deterministic copy.
    useAuthStore.setState({
      user: {
        id: "u-1",
        email: "alice@example.com",
        displayName: "Alice",
        role: "developer",
        isActive: true,
        isSuperuser: false,
        teamId: null,
      },
      accessToken: "token",
      status: "authenticated",
      isAuthenticated: true,
    });
  });

  it("renders both connected identities with Unlink buttons", async () => {
    mockedList.mockResolvedValueOnce({
      items: [
        identity({ id: "id-github-1", provider: "github" }),
        identity({
          id: "id-google-1",
          provider: "google",
          provider_email: "alice.gmail@example.com",
        }),
      ],
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getAllByTestId("profile-identity-row")).toHaveLength(2);
    });
    // Each row exposes its provider via data-attribute for stable assertions.
    const rows = screen.getAllByTestId("profile-identity-row");
    expect(rows[0].getAttribute("data-provider")).toBe("github");
    expect(rows[1].getAttribute("data-provider")).toBe("google");
    expect(screen.getAllByTestId("profile-identity-unlink")).toHaveLength(2);
    expect(screen.getAllByText("alice@example.com").length).toBeGreaterThan(0);
    expect(screen.getByText("alice.gmail@example.com")).toBeInTheDocument();
  });

  it("unlinks a provider after inline confirmation: API + toast + row removed", async () => {
    const rows = [
      identity({ id: "id-github-1", provider: "github" }),
      identity({ id: "id-google-1", provider: "google" }),
    ];
    // First call → both rows. After unlink the cache invalidation refetches
    // a list that no longer contains the GitHub identity.
    mockedList.mockResolvedValueOnce({ items: rows });
    mockedList.mockResolvedValue({ items: [rows[1]] });
    mockedUnlink.mockResolvedValueOnce(undefined);

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getAllByTestId("profile-identity-row")).toHaveLength(2);
    });

    // Click the FIRST row's Unlink → confirmation strip appears.
    await user.click(screen.getAllByTestId("profile-identity-unlink")[0]);
    expect(
      await screen.findByTestId("profile-identity-confirm-strip"),
    ).toBeInTheDocument();

    await user.click(screen.getByTestId("profile-identity-confirm-ok"));

    await waitFor(() => {
      expect(mockedUnlink).toHaveBeenCalledWith("id-github-1");
    });

    // After invalidation the second list call resolves with one row.
    await waitFor(() => {
      const remaining = screen.getAllByTestId("profile-identity-row");
      expect(remaining).toHaveLength(1);
      expect(remaining[0].getAttribute("data-provider")).toBe("google");
    });

    const toast = screen.getByTestId("admin-toast");
    expect(toast).toHaveAttribute("data-tone", "success");
    expect(toast).toHaveAttribute("data-toast-key", "unlinked");
  });

  it("shows the inline blocks-login alert on 409 and keeps the row", async () => {
    const rows = [identity({ id: "only-github", provider: "github" })];
    mockedList.mockResolvedValue({ items: rows });

    const blocks = new ProblemError("Cannot remove last authentication method", {
      status: 409,
      title: "Cannot remove last authentication method",
      detail: "Cannot remove last authentication method",
      problem: {
        type: OAUTH_UNLINK_BLOCKS_LOGIN_TYPE,
        title: "Cannot remove last authentication method",
        status: 409,
        detail: "Cannot remove last authentication method",
      },
    });
    mockedUnlink.mockRejectedValueOnce(blocks);

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("profile-identity-row")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("profile-identity-unlink"));
    await user.click(
      await screen.findByTestId("profile-identity-confirm-ok"),
    );

    // Inline alert renders in-place above the row; the row itself stays.
    await waitFor(() => {
      expect(
        screen.getByTestId("profile-unlink-blocks-login"),
      ).toBeInTheDocument();
    });
    expect(screen.getByTestId("profile-identity-row")).toBeInTheDocument();

    // No toast for the blocks-login case — the user must read the inline copy.
    expect(screen.queryByTestId("admin-toast")).not.toBeInTheDocument();
  });

  it("renders the empty state when no providers are linked", async () => {
    mockedList.mockResolvedValue({ items: [] });

    renderPage();

    await waitFor(() => {
      expect(
        screen.getByTestId("profile-identities-empty"),
      ).toBeInTheDocument();
    });
    expect(screen.queryByTestId("profile-identity-row")).not.toBeInTheDocument();
  });

  it("surfaces a generic error toast when the unlink mutation fails for unrelated reasons", async () => {
    const rows = [identity({ id: "id-github-1", provider: "github" })];
    mockedList.mockResolvedValue({ items: rows });
    mockedUnlink.mockRejectedValueOnce(new Error("boom"));

    const user = userEvent.setup();
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("profile-identity-row")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("profile-identity-unlink"));
    await user.click(
      await screen.findByTestId("profile-identity-confirm-ok"),
    );

    await waitFor(() => {
      const toast = screen.getByTestId("admin-toast");
      expect(toast).toHaveAttribute("data-tone", "error");
      expect(toast).toHaveAttribute("data-toast-key", "unlink_failed");
    });
    // Row stays — generic errors don't mutate cache.
    expect(screen.getByTestId("profile-identity-row")).toBeInTheDocument();
    expect(
      screen.queryByTestId("profile-unlink-blocks-login"),
    ).not.toBeInTheDocument();
  });
});
