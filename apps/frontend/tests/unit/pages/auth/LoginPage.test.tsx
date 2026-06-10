import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/components/AppProviders";
import { LoginPage } from "@/pages/auth/LoginPage";
import { ProblemError } from "@/lib/problem";
import { useAuthStore, type AuthUser } from "@/stores/authStore";

// Mock the wire layer so the unit test never touches axios / network. The
// integration coverage for the real interceptor is in api.test.ts; here we
// only care about the page's behavioural contract.
vi.mock("@/lib/api", () => ({
  postLogin: vi.fn(),
  fetchMe: vi.fn(),
  postRegister: vi.fn(),
  postLogout: vi.fn(),
  fetchOAuthProviders: vi.fn(),
}));

import { fetchMe, fetchOAuthProviders, postLogin } from "@/lib/api";
const mockedPostLogin = vi.mocked(postLogin);
const mockedFetchMe = vi.mocked(fetchMe);
const mockedFetchProviders = vi.mocked(fetchOAuthProviders);

/** Shorthand for the GET /auth/oauth/providers wire shape. */
function providersResponse(github: boolean, google: boolean) {
  return {
    providers: [
      { provider: "github" as const, configured: github },
      { provider: "google" as const, configured: google },
    ],
  };
}

const sampleUser: AuthUser = {
  id: "u-1",
  email: "alice@example.com",
  displayName: "Alice",
  role: "developer",
  isActive: true,
  isSuperuser: false,
  teamId: null,
};

function renderLogin(initialPath: string = "/login") {
  return render(
    <AppProviders router="none">
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div data-testid="home-stub" />} />
          <Route path="/register" element={<div data-testid="register-stub" />} />
          <Route
            path="/forgot-password"
            element={<div data-testid="forgot-stub" />}
          />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: null,
      status: "anonymous",
      isAuthenticated: false,
    });
    mockedPostLogin.mockReset();
    mockedFetchMe.mockReset();
    mockedFetchProviders.mockReset();
    // M-15 default: both providers configured so the pre-existing OAuth
    // assertions keep exercising the rendered-buttons path. Provider-combo
    // cases override this per test.
    mockedFetchProviders.mockResolvedValue(providersResponse(true, true));
  });

  it("renders email + password fields and a submit button", () => {
    renderLogin();
    expect(screen.getByTestId("login-page")).toBeInTheDocument();
    expect(screen.getByTestId("login-email")).toBeInTheDocument();
    expect(screen.getByTestId("login-password")).toBeInTheDocument();
    expect(screen.getByTestId("login-submit")).toBeInTheDocument();
  });

  it("blocks submit when email is invalid (zod inline error, no network)", async () => {
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByTestId("login-email"), "not-an-email");
    await user.type(
      screen.getByTestId("login-password"),
      "longenoughpassword12",
    );
    await user.click(screen.getByTestId("login-submit"));

    expect(await screen.findByText(/valid email/i)).toBeInTheDocument();
    expect(mockedPostLogin).not.toHaveBeenCalled();
  });

  it("blocks submit when password is shorter than 8 chars", async () => {
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    // 7 characters — one short of the policy floor.
    await user.type(screen.getByTestId("login-password"), "seven77");
    await user.click(screen.getByTestId("login-submit"));

    expect(
      await screen.findByText(/at least 8 characters/i),
    ).toBeInTheDocument();
    expect(mockedPostLogin).not.toHaveBeenCalled();
  });

  it("on success stores token + user, sets status, redirects to /", async () => {
    mockedPostLogin.mockResolvedValueOnce({
      access_token: "tok-1",
      token_type: "bearer",
      expires_in: 1800,
    });
    mockedFetchMe.mockResolvedValueOnce(sampleUser);

    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    await user.type(
      screen.getByTestId("login-password"),
      "correct-horse-battery-staple",
    );
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("home-stub")).toBeInTheDocument();
    });
    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("tok-1");
    expect(state.status).toBe("authenticated");
    expect(state.isAuthenticated).toBe(true);
    expect(state.user?.email).toBe("alice@example.com");
  });

  it("renders RFC 7807 detail in the alert on 401", async () => {
    mockedPostLogin.mockRejectedValueOnce(
      new ProblemError("invalid email or password", {
        status: 401,
        title: "invalid_credentials",
        detail: "invalid email or password",
        problem: {
          type: "about:blank",
          title: "invalid_credentials",
          status: 401,
          detail: "invalid email or password",
          instance: "/auth/login",
        },
      }),
    );

    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    await user.type(
      screen.getByTestId("login-password"),
      "wrong-password-but-12",
    );
    await user.click(screen.getByTestId("login-submit"));

    const alert = await screen.findByTestId("login-error");
    expect(alert).toHaveTextContent(/invalid email or password/i);
    expect(screen.queryByTestId("home-stub")).not.toBeInTheDocument();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(mockedFetchMe).not.toHaveBeenCalled();
  });

  it("falls back to a generic network message on transport failure", async () => {
    mockedPostLogin.mockRejectedValueOnce(
      new ProblemError("Failed to fetch", {
        status: 0,
        title: "network",
        detail: "Failed to fetch",
        problem: null,
      }),
    );

    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    await user.type(
      screen.getByTestId("login-password"),
      "correct-horse-battery",
    );
    await user.click(screen.getByTestId("login-submit"));

    const alert = await screen.findByTestId("login-error");
    expect(alert).toHaveTextContent(/Failed to fetch/i);
  });

  it("links to /register and /forgot-password", () => {
    renderLogin();
    expect(screen.getByTestId("login-signup-link")).toHaveAttribute(
      "href",
      "/register",
    );
    expect(screen.getByTestId("login-forgot-link")).toHaveAttribute(
      "href",
      "/forgot-password",
    );
  });

  it("auto-redirects to / when user arrives already authenticated", async () => {
    useAuthStore.setState({
      user: sampleUser,
      accessToken: "tok-existing",
      status: "authenticated",
      isAuthenticated: true,
    });
    renderLogin();
    await waitFor(() => {
      expect(screen.getByTestId("home-stub")).toBeInTheDocument();
    });
  });

  it("L-1: ?registered=1 query → renders success alert (default variant, not error)", () => {
    renderLogin("/login?registered=1");
    const success = screen.getByTestId("login-registered-success");
    expect(success).toBeInTheDocument();
    expect(success).toHaveTextContent(/please sign in/i);
    // Not the destructive error alert.
    expect(screen.queryByTestId("login-error")).not.toBeInTheDocument();
  });

  it("L-1: bare /login (no ?registered) → no success alert", () => {
    renderLogin("/login");
    expect(
      screen.queryByTestId("login-registered-success"),
    ).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // chore B — OAuth buttons + ?error= mapping
  // -------------------------------------------------------------------------

  it("renders Sign-in-with-GitHub and Sign-in-with-Google buttons when both are configured", async () => {
    renderLogin();
    // Buttons render only after GET /auth/oauth/providers resolves (M-15).
    expect(await screen.findByTestId("login-oauth-github")).toBeInTheDocument();
    expect(screen.getByTestId("login-oauth-google")).toBeInTheDocument();
    expect(screen.getByTestId("login-oauth-divider")).toBeInTheDocument();
  });

  it("M-15: renders only the configured provider (github-only)", async () => {
    mockedFetchProviders.mockResolvedValue(providersResponse(true, false));
    renderLogin();

    expect(await screen.findByTestId("login-oauth-github")).toBeInTheDocument();
    expect(screen.queryByTestId("login-oauth-google")).not.toBeInTheDocument();
    // Divider still shows — there IS an alternative sign-in method.
    expect(screen.getByTestId("login-oauth-divider")).toBeInTheDocument();
  });

  it("M-15: hides the entire OAuth section (divider included) when no provider is configured", async () => {
    mockedFetchProviders.mockResolvedValue(providersResponse(false, false));
    renderLogin();

    // Wait for the providers query to settle, then assert nothing rendered.
    await waitFor(() => {
      expect(mockedFetchProviders).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("login-oauth-github")).not.toBeInTheDocument();
    expect(screen.queryByTestId("login-oauth-google")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("login-oauth-divider"),
    ).not.toBeInTheDocument();
    // The password form is unaffected.
    expect(screen.getByTestId("login-form")).toBeInTheDocument();
  });

  it("M-15: fails closed — no OAuth buttons when the providers fetch errors", async () => {
    // Reject EVERY attempt (the query client retries once).
    mockedFetchProviders.mockReset();
    mockedFetchProviders.mockRejectedValue(
      new ProblemError("boom", {
        status: 500,
        title: "internal",
        detail: "boom",
        problem: null,
      }),
    );
    renderLogin();

    await waitFor(() => {
      expect(mockedFetchProviders).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("login-oauth-github")).not.toBeInTheDocument();
    expect(screen.queryByTestId("login-oauth-google")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("login-oauth-divider"),
    ).not.toBeInTheDocument();
    // Email + password sign-in still fully available.
    expect(screen.getByTestId("login-form")).toBeInTheDocument();
  });

  it("OAuth click navigates to /auth/oauth/<provider>/authorize with redirect_after", async () => {
    // Stub window.location.href so the test never actually navigates. We
    // assign a plain mutable container; jsdom's default is a Location object
    // that throws on cross-origin assignment, but defining `href` as a sink
    // avoids the navigation while keeping the assertion intact.
    const original = window.location;
    const sink = { href: "" };
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: sink,
    });

    try {
      const user = userEvent.setup();
      renderLogin("/login?redirect_after=%2Fprojects");

      await user.click(await screen.findByTestId("login-oauth-github"));
      expect(sink.href).toMatch(/\/auth\/oauth\/github\/authorize/);
      // redirect_after is propagated url-encoded.
      expect(sink.href).toMatch(/redirect_after=%2Fprojects/);
    } finally {
      Object.defineProperty(window, "location", {
        configurable: true,
        writable: true,
        value: original,
      });
    }
  });

  it("renders the OAuth error banner when ?error=oauth_denied", () => {
    renderLogin("/login?error=oauth_denied");
    const err = screen.getByTestId("login-oauth-error");
    expect(err).toBeInTheDocument();
    expect(err).toHaveTextContent(/cancelled|denied/i);
  });

  it("falls back to a generic banner for an unknown oauth_* code", () => {
    renderLogin("/login?error=oauth_something_new");
    const err = screen.getByTestId("login-oauth-error");
    expect(err).toBeInTheDocument();
    // The "unknown" message — we don't trust the raw query verbatim.
    expect(err).toHaveTextContent(/something went wrong/i);
  });

  it("ignores non-oauth ?error= values (no banner)", () => {
    renderLogin("/login?error=<script>alert(1)</script>");
    expect(
      screen.queryByTestId("login-oauth-error"),
    ).not.toBeInTheDocument();
  });

  it("L-1: success alert hides once a real submit error replaces it", async () => {
    mockedPostLogin.mockRejectedValueOnce(
      new ProblemError("invalid email or password", {
        status: 401,
        title: "invalid_credentials",
        detail: "invalid email or password",
        problem: {
          type: "about:blank",
          title: "invalid_credentials",
          status: 401,
          detail: "invalid email or password",
          instance: "/auth/login",
        },
      }),
    );

    const user = userEvent.setup();
    renderLogin("/login?registered=1");
    expect(
      screen.getByTestId("login-registered-success"),
    ).toBeInTheDocument();

    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    await user.type(
      screen.getByTestId("login-password"),
      "wrong-password-12chars",
    );
    await user.click(screen.getByTestId("login-submit"));

    await screen.findByTestId("login-error");
    // Success alert is suppressed once the user has interacted and got a real
    // error — keeps the page focused on the actionable failure.
    expect(
      screen.queryByTestId("login-registered-success"),
    ).not.toBeInTheDocument();
  });
});
