import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/components/AppProviders";
import { RegisterPage } from "@/pages/auth/RegisterPage";
import { ProblemError } from "@/lib/problem";
import { useAuthStore, type AuthUser } from "@/stores/authStore";

vi.mock("@/lib/api", () => ({
  postLogin: vi.fn(),
  postRegister: vi.fn(),
  fetchMe: vi.fn(),
  postLogout: vi.fn(),
}));

import { fetchMe, postLogin, postRegister } from "@/lib/api";

const mockedPostLogin = vi.mocked(postLogin);
const mockedPostRegister = vi.mocked(postRegister);
const mockedFetchMe = vi.mocked(fetchMe);

const sampleUser: AuthUser = {
  id: "u-1",
  email: "alice@example.com",
  displayName: "Alice",
  role: "developer",
  isActive: true,
  isSuperuser: false,
  teamId: null,
};

function LocationProbe() {
  const loc = useLocation();
  return (
    <span data-testid="login-stub-search" data-search={loc.search}>
      {loc.search}
    </span>
  );
}

function renderRegister() {
  return render(
    <AppProviders router="none">
      <MemoryRouter initialEntries={["/register"]}>
        <Routes>
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/" element={<div data-testid="home-stub" />} />
          <Route
            path="/login"
            element={
              <div data-testid="login-stub">
                <LocationProbe />
              </div>
            }
          />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("RegisterPage", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: null,
      status: "anonymous",
      isAuthenticated: false,
    });
    mockedPostLogin.mockReset();
    mockedPostRegister.mockReset();
    mockedFetchMe.mockReset();
  });

  it("blocks submit when display name is empty", async () => {
    const user = userEvent.setup();
    renderRegister();
    await user.type(screen.getByTestId("register-email"), "alice@example.com");
    await user.type(
      screen.getByTestId("register-password"),
      "longerthantwelvechars",
    );
    await user.click(screen.getByTestId("register-submit"));

    expect(await screen.findByText(/required/i)).toBeInTheDocument();
    expect(mockedPostRegister).not.toHaveBeenCalled();
  });

  it("blocks submit when password is shorter than 8 chars (client-side)", async () => {
    const user = userEvent.setup();
    renderRegister();
    await user.type(screen.getByTestId("register-display-name"), "Alice");
    await user.type(screen.getByTestId("register-email"), "alice@example.com");
    // 7 chars — short of the 8-char floor.
    await user.type(screen.getByTestId("register-password"), "seven77");
    await user.click(screen.getByTestId("register-submit"));

    expect(
      await screen.findByText(/at least 8 characters/i),
    ).toBeInTheDocument();
    expect(mockedPostRegister).not.toHaveBeenCalled();
  });

  it("registers then auto-logs-in, hydrates /me, redirects to /", async () => {
    mockedPostRegister.mockResolvedValueOnce({
      id: "u-1",
      email: "alice@example.com",
      full_name: "Alice",
      is_active: true,
      is_superuser: false,
      created_at: "2026-05-05T00:00:00Z",
    });
    mockedPostLogin.mockResolvedValueOnce({
      access_token: "tok-2",
      token_type: "bearer",
      expires_in: 1800,
    });
    mockedFetchMe.mockResolvedValueOnce(sampleUser);

    const user = userEvent.setup();
    renderRegister();
    await user.type(screen.getByTestId("register-display-name"), "Alice");
    await user.type(screen.getByTestId("register-email"), "alice@example.com");
    await user.type(
      screen.getByTestId("register-password"),
      "correct-horse-battery-staple",
    );
    await user.click(screen.getByTestId("register-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("home-stub")).toBeInTheDocument();
    });
    expect(mockedPostRegister).toHaveBeenCalledTimes(1);
    expect(mockedPostLogin).toHaveBeenCalledTimes(1);
    expect(mockedFetchMe).toHaveBeenCalledTimes(1);
    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("tok-2");
    expect(state.status).toBe("authenticated");
    expect(state.user?.displayName).toBe("Alice");
  });

  it("surfaces backend 12-char policy error from RFC 7807 detail", async () => {
    mockedPostRegister.mockRejectedValueOnce(
      new ProblemError("password must be at least 12 characters", {
        status: 422,
        title: "validation_error",
        detail: "password must be at least 12 characters",
        problem: {
          type: "about:blank",
          title: "validation_error",
          status: 422,
          detail: "password must be at least 12 characters",
          instance: "/auth/register",
        },
      }),
    );

    const user = userEvent.setup();
    renderRegister();
    await user.type(screen.getByTestId("register-display-name"), "Alice");
    await user.type(screen.getByTestId("register-email"), "alice@example.com");
    // 12 chars — passes the client gate; backend still rejects (e.g. policy
    // dictionary check). The alert should surface the RFC 7807 detail.
    await user.type(screen.getByTestId("register-password"), "twelvechars1");
    await user.click(screen.getByTestId("register-submit"));

    const alert = await screen.findByTestId("register-error");
    expect(alert).toHaveTextContent(/at least 12 characters/i);
    expect(mockedPostLogin).not.toHaveBeenCalled();
  });

  it("links back to /login", () => {
    renderRegister();
    expect(screen.getByTestId("register-signin-link")).toHaveAttribute(
      "href",
      "/login",
    );
  });

  it("L-1: auto-login 429 → navigates to /login?registered=1 (no error)", async () => {
    // Account creation succeeds, but the same IP just hit /auth/login's rate
    // limiter (5/min). The user should NOT be stranded on /register seeing a
    // confusing 429 alert — they should land on /login with the success flag.
    mockedPostRegister.mockResolvedValueOnce({
      id: "u-2",
      email: "bob@example.com",
      full_name: "Bob",
      is_active: true,
      is_superuser: false,
      created_at: "2026-05-06T00:00:00Z",
    });
    mockedPostLogin.mockRejectedValueOnce(
      new ProblemError("Too Many Requests", {
        status: 429,
        title: "rate_limited",
        detail: "Too Many Requests",
        problem: {
          type: "about:blank",
          title: "rate_limited",
          status: 429,
          detail: "Too Many Requests",
          instance: "/auth/login",
        },
      }),
    );

    const user = userEvent.setup();
    renderRegister();
    await user.type(screen.getByTestId("register-display-name"), "Bob");
    await user.type(screen.getByTestId("register-email"), "bob@example.com");
    await user.type(
      screen.getByTestId("register-password"),
      "correct-horse-battery-staple",
    );
    await user.click(screen.getByTestId("register-submit"));

    // Lands on /login with the ?registered=1 flag.
    await waitFor(() => {
      expect(screen.getByTestId("login-stub")).toBeInTheDocument();
    });
    const probe = screen.getByTestId("login-stub-search");
    expect(probe.getAttribute("data-search")).toBe("?registered=1");

    // No error alert leaked to the user — they did succeed in creating the
    // account; only the auto-login leg failed.
    expect(screen.queryByTestId("register-error")).not.toBeInTheDocument();

    // No fetchMe call (auto-login bailed before it).
    expect(mockedFetchMe).not.toHaveBeenCalled();

    // Auth state is still anonymous — the user has to sign in deliberately.
    expect(useAuthStore.getState().status).toBe("anonymous");
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("L-1: still surfaces a register failure on the register form", async () => {
    // Regression guard: register() failures stay on /register with the alert.
    mockedPostRegister.mockRejectedValueOnce(
      new ProblemError("email already registered", {
        status: 409,
        title: "email_taken",
        detail: "email already registered",
        problem: {
          type: "about:blank",
          title: "email_taken",
          status: 409,
          detail: "email already registered",
          instance: "/auth/register",
        },
      }),
    );

    const user = userEvent.setup();
    renderRegister();
    await user.type(screen.getByTestId("register-display-name"), "Carol");
    await user.type(screen.getByTestId("register-email"), "carol@example.com");
    await user.type(
      screen.getByTestId("register-password"),
      "correct-horse-battery-staple",
    );
    await user.click(screen.getByTestId("register-submit"));

    const alert = await screen.findByTestId("register-error");
    expect(alert).toHaveTextContent(/already registered/i);
    // Crucially, the user did NOT bounce to /login.
    expect(screen.queryByTestId("login-stub")).not.toBeInTheDocument();
    expect(mockedPostLogin).not.toHaveBeenCalled();
  });
});
