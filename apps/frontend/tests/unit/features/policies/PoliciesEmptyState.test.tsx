/**
 * What the policy list says when it is empty (C3).
 *
 * The old string was an absence and an instruction in one sentence, and the
 * instruction was wrong for a developer: they can pick a team and open the
 * editor, and find it read-only. Which of the two hints renders is a branch,
 * and a branch with no test is how the copy quietly goes back.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PoliciesPage } from "@/features/policies/PoliciesPage";
import { useAuthStore, type AuthUser } from "@/stores/authStore";

const apiGet = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      get: (url: string, config?: { params?: Record<string, unknown> }) =>
        apiGet(url, config?.params ?? {}),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  };
});

function user(role: AuthUser["role"]): AuthUser {
  return {
    id: "u-1",
    email: "alice@example.com",
    displayName: "Alice",
    role,
    isActive: true,
    isSuperuser: false,
    teamId: "t-1",
    teams: [{ id: "t-1", name: "Platform", role }],
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/policies"]}>
        <PoliciesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PoliciesPage empty state", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiGet.mockResolvedValue({
      data: { items: [], total: 0, page: 1, page_size: 50 },
    });
  });
  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it("tells a team administrator where to write one", async () => {
    useAuthStore.setState({
      user: user("team_admin"),
      accessToken: "tok",
      status: "authenticated",
      isAuthenticated: true,
    });

    renderPage();

    const empty = await screen.findByTestId("policies-empty");
    expect(empty.textContent).toContain("Pick a team above");
    // And says what is protecting them meanwhile, so an empty list does not
    // read as "nothing is checked".
    expect(empty.textContent).toContain("built-in license categories");
  });

  it("does not tell a developer to do what they cannot", async () => {
    useAuthStore.setState({
      user: user("developer"),
      accessToken: "tok",
      status: "authenticated",
      isAuthenticated: true,
    });

    renderPage();

    const empty = await screen.findByTestId("policies-empty");
    expect(empty.textContent).toContain("team administrator's job");
    expect(empty.textContent).not.toContain("Pick a team above");
    expect(empty.textContent).toContain("built-in license categories");
  });
});
