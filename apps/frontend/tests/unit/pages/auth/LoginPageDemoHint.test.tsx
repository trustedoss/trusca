/**
 * LoginPage demo credentials hint — v2.1 Track B (B5).
 *
 * The hint box (seeded demo account + one-click "fill") renders ONLY when the
 * backend reports `demo_read_only` via /health. A normal deploy must never leak
 * demo account hints. We mock the wire layer (`@/lib/api`) so `useDemoMode`
 * resolves deterministically and `postLogin`/`fetchMe` stay inert.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/components/AppProviders";
import {
  DEMO_LOGIN_EMAIL,
  DEMO_LOGIN_PASSWORD,
} from "@/pages/auth/DemoCredentialsHint";
import { LoginPage } from "@/pages/auth/LoginPage";
import { useAuthStore } from "@/stores/authStore";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
  postLogin: vi.fn(),
  fetchMe: vi.fn(),
  postRegister: vi.fn(),
  postLogout: vi.fn(),
  fetchOAuthProviders: vi.fn(),
}));

import { api, fetchOAuthProviders } from "@/lib/api";

const mockedGet = vi.mocked(api.get);
const mockedFetchProviders = vi.mocked(fetchOAuthProviders);

/** Resolve /health with a fixed demo flag. */
function stubHealth(demoReadOnly: boolean) {
  mockedGet.mockResolvedValue({ data: { status: "ok", demo_read_only: demoReadOnly } });
}

function renderLogin() {
  return render(
    <AppProviders router="none">
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div data-testid="home-stub" />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("LoginPage demo credentials hint", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: null,
      status: "anonymous",
      isAuthenticated: false,
    });
    mockedGet.mockReset();
    mockedFetchProviders.mockReset();
    // No OAuth buttons — keeps the test focused on the demo hint.
    mockedFetchProviders.mockResolvedValue({
      providers: [
        { provider: "github" as const, configured: false },
        { provider: "google" as const, configured: false },
      ],
    });
  });

  it("renders the demo hint when the backend is in read-only demo mode", async () => {
    stubHealth(true);
    renderLogin();

    const hint = await screen.findByTestId("login-demo-hint");
    expect(hint).toBeInTheDocument();
    expect(screen.getByTestId("login-demo-email")).toHaveTextContent(
      DEMO_LOGIN_EMAIL,
    );
    expect(screen.getByTestId("login-demo-password")).toHaveTextContent(
      DEMO_LOGIN_PASSWORD,
    );
  });

  it("does NOT render the demo hint on a normal (non-demo) deploy", async () => {
    stubHealth(false);
    renderLogin();

    // Wait for /health to resolve, then assert the hint stays hidden.
    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith("/health");
    });
    expect(screen.queryByTestId("login-demo-hint")).not.toBeInTheDocument();
  });

  it("the fill button populates the email + password fields", async () => {
    stubHealth(true);
    const user = userEvent.setup();
    renderLogin();

    await user.click(await screen.findByTestId("login-demo-fill"));

    expect(screen.getByTestId("login-email")).toHaveValue(DEMO_LOGIN_EMAIL);
    expect(screen.getByTestId("login-password")).toHaveValue(
      DEMO_LOGIN_PASSWORD,
    );
  });
});
