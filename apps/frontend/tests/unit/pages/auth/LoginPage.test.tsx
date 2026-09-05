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
vi.mock("@/lib/api", async (importOriginal) => ({
  // The real module first, then the stubs over it. A whole-module factory
  // replaces every export, so one added later arrives here as undefined and
  // the page calls it: `isMfaRequired` did exactly that, and every response
  // then looked like a completed sign-in, which is the branch this file
  // exists to check.
  ...(await importOriginal<typeof import("@/lib/api")>()),
  postLogin: vi.fn(),
  postMfaVerify: vi.fn(),
  fetchMe: vi.fn(),
  postRegister: vi.fn(),
  postLogout: vi.fn(),
  fetchOAuthProviders: vi.fn(),
}));

import {
  fetchMe,
  fetchOAuthProviders,
  postLogin,
  postMfaVerify,
} from "@/lib/api";
const mockedPostLogin = vi.mocked(postLogin);
const mockedPostMfaVerify = vi.mocked(postMfaVerify);
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

function renderLogin(
  initialPath: string = "/login",
  // A5: the guard and the expiry banner both read router state, which is
  // what `RequireAuth` and `AuthExpiredListener` put there.
  state?: { from?: unknown; expired?: unknown },
) {
  return render(
    <AppProviders router="none">
      <MemoryRouter
        initialEntries={[
          state ? { pathname: initialPath, state } : initialPath,
        ]}
      >
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div data-testid="home-stub" />} />
          <Route
            path="/projects/:id"
            element={<div data-testid="project-stub" />}
          />
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

  it("tells a refused sign-in how long to wait and offers a reset", async () => {
    // The 429 body says nothing about whether the address exists, on purpose.
    // The wait and the offer to reset are the only things in it a person can
    // act on, so a bare "too many requests" would leave somebody who mistyped
    // their password with no idea what to do next.
    mockedPostLogin.mockRejectedValueOnce(
      new ProblemError("too many attempts", {
        status: 429,
        title: "Too Many Attempts",
        detail: "Too many failed sign-in attempts.",
        problem: {
          type: "https://trustedoss.dev/problems/too-many-attempts",
          title: "Too Many Attempts",
          status: 429,
          detail: "Too many failed sign-in attempts.",
          instance: "/auth/login",
          retry_after_seconds: 900,
        },
      }),
    );

    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    await user.type(screen.getByTestId("login-password"), "wrong-password-but-12");
    await user.click(screen.getByTestId("login-submit"));

    const alert = await screen.findByTestId("login-error");
    expect(alert).toHaveTextContent(/15 minutes/i);
    expect(alert).toHaveTextContent(/reset/i);
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("leaves the per-IP limiter's 429 to the generic message", async () => {
    // Two different 429s reach this page. The per-IP limiter counts every
    // attempt, successful ones included, so telling that caller their password
    // was wrong too many times would be untrue and would send them to the reset
    // form for no reason. Branching on the status code alone could not tell
    // them apart, which is why this asserts on the one that must not match.
    mockedPostLogin.mockRejectedValueOnce(
      new ProblemError("rate limit exceeded", {
        status: 429,
        title: "Too Many Requests",
        detail: "Rate limit exceeded",
        problem: {
          type: "about:blank",
          title: "Too Many Requests",
          status: 429,
          detail: "Rate limit exceeded",
          instance: "/auth/login",
        },
      }),
    );

    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    await user.type(screen.getByTestId("login-password"), "wrong-password-but-12");
    await user.click(screen.getByTestId("login-submit"));

    const alert = await screen.findByTestId("login-error");
    expect(alert.textContent ?? "").not.toMatch(/failed sign-in attempts/i);
  });

  it("still says what to do when the wait is missing from the body", async () => {
    // The hint is an extension field, so a proxy that strips unknown keys or a
    // future backend that stops sending it leaves the number absent. Falling
    // through to the generic rate-limit sentence would drop the reset offer
    // with it, which is the part that matters most.
    mockedPostLogin.mockRejectedValueOnce(
      new ProblemError("too many attempts", {
        status: 429,
        title: "Too Many Attempts",
        detail: "Too many failed sign-in attempts.",
        problem: {
          type: "https://trustedoss.dev/problems/too-many-attempts",
          title: "Too Many Attempts",
          status: 429,
          detail: "Too many failed sign-in attempts.",
          instance: "/auth/login",
        },
      }),
    );

    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    await user.type(screen.getByTestId("login-password"), "wrong-password-but-12");
    await user.click(screen.getByTestId("login-submit"));

    const alert = await screen.findByTestId("login-error");
    expect(alert).toHaveTextContent(/reset/i);
    expect(alert.textContent).not.toMatch(/NaN|undefined/);
  });

  it("asks for a code instead of signing in when a second factor is owed", async () => {
    // The password form gives way to the code form rather than sitting beside
    // it: leaving the fields visible invites a retype, which starts a fresh
    // sign-in and discards the pending token being asked for.
    mockedPostLogin.mockResolvedValueOnce({
      mfa_required: true,
      mfa_token: "pending-token",
      expires_in: 300,
    });

    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    await user.type(screen.getByTestId("login-password"), "right-password-12");
    await user.click(screen.getByTestId("login-submit"));

    expect(await screen.findByTestId("login-mfa-form")).toBeInTheDocument();
    expect(screen.queryByTestId("login-password")).not.toBeInTheDocument();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(mockedFetchMe).not.toHaveBeenCalled();
  });

  it("signs in once the code is accepted", async () => {
    mockedPostLogin.mockResolvedValueOnce({
      mfa_required: true,
      mfa_token: "pending-token",
      expires_in: 300,
    });
    mockedPostMfaVerify.mockResolvedValueOnce({
      access_token: "real-token",
      token_type: "bearer",
      expires_in: 1800,
    });
    mockedFetchMe.mockResolvedValueOnce(sampleUser);

    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    await user.type(screen.getByTestId("login-password"), "right-password-12");
    await user.click(screen.getByTestId("login-submit"));

    await user.type(await screen.findByTestId("login-mfa-code"), "123456");
    await user.click(screen.getByTestId("login-mfa-submit"));

    await waitFor(() => {
      expect(useAuthStore.getState().isAuthenticated).toBe(true);
    });
    expect(mockedPostMfaVerify).toHaveBeenCalledWith({
      mfa_token: "pending-token",
      code: "123456",
    });
  });

  it("keeps the code step open when the code is wrong", async () => {
    // Sending somebody back to the password form would discard a pending token
    // that is still good, and the next code from their app is thirty seconds
    // away. The field is cleared so the wrong digits are not resubmitted.
    mockedPostLogin.mockResolvedValueOnce({
      mfa_required: true,
      mfa_token: "pending-token",
      expires_in: 300,
    });
    mockedPostMfaVerify.mockRejectedValueOnce(
      new ProblemError("invalid code", {
        status: 401,
        title: "Invalid Credentials",
        detail: "invalid code",
        problem: {
          type: "about:blank",
          title: "Invalid Credentials",
          status: 401,
          detail: "invalid code",
          instance: "/auth/mfa/verify",
        },
      }),
    );

    const user = userEvent.setup();
    renderLogin();
    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    await user.type(screen.getByTestId("login-password"), "right-password-12");
    await user.click(screen.getByTestId("login-submit"));

    await user.type(await screen.findByTestId("login-mfa-code"), "000000");
    await user.click(screen.getByTestId("login-mfa-submit"));

    expect(await screen.findByTestId("login-error")).toBeInTheDocument();
    expect(screen.getByTestId("login-mfa-form")).toBeInTheDocument();
    expect(screen.getByTestId("login-mfa-code")).toHaveValue("");
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
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
    // axios's own "Failed to fetch" used to reach the screen. The transport
    // failure is now named in the user's language.
    expect(alert).toHaveTextContent("Network error");
    expect(alert).not.toHaveTextContent(/Failed to fetch/i);
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

  // ─── A5: say why, and go back where the user was ───────────────────────

  it("says a session ended when that is why the user is here", async () => {
    renderLogin("/login", { expired: true, from: "/projects/42" });

    const banner = await screen.findByTestId("login-session-expired");
    expect(banner.textContent).toContain("Your session ended");
  });

  it("says nothing about sessions when the user came here on purpose", () => {
    renderLogin();

    expect(screen.queryByTestId("login-session-expired")).toBeNull();
  });

  it("returns the user to the page they were sent away from", async () => {
    const user = userEvent.setup();
    mockedPostLogin.mockResolvedValue({
      access_token: "t",
      token_type: "bearer",
      expires_in: 1800,
    });
    mockedFetchMe.mockResolvedValue(sampleUser);

    renderLogin("/login", { from: "/projects/42?tab=components" });

    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    await user.type(screen.getByTestId("login-password"), "correct-horse-1");
    await user.click(screen.getByTestId("login-submit"));

    // Landing on the dashboard instead would mean the deep link the user
    // followed was thrown away by the sign-in it triggered.
    await screen.findByTestId("project-stub");
  });

  it("refuses to hand the user to another origin after signing in", async () => {
    // The attack is a link that carries the return target: the victim signs
    // in to the real product, and the product then delivers them to a
    // lookalike one keystroke later.
    //
    // Unlike the three above, this one passes against the code before A5
    // too, because that code ignored `from` entirely and always went to the
    // dashboard. It is here to pin the property, not to prove the change:
    // it is what fails the day someone reaches for `state.from` directly.
    const user = userEvent.setup();
    mockedPostLogin.mockResolvedValue({
      access_token: "t",
      token_type: "bearer",
      expires_in: 1800,
    });
    mockedFetchMe.mockResolvedValue(sampleUser);

    renderLogin("/login", { from: "//evil.example/steal" });

    await user.type(screen.getByTestId("login-email"), "alice@example.com");
    await user.type(screen.getByTestId("login-password"), "correct-horse-1");
    await user.click(screen.getByTestId("login-submit"));

    await screen.findByTestId("home-stub");
  });

  it("puts a crafted ?redirect_after through the same guard", async () => {
    // This is the half an attacker can set: a link carries a query string,
    // and cannot carry router state. It reached the provider round-trip
    // unchecked, and the backend obeyed whatever came back, so a real OAuth
    // sign-in delivered the user off-site. The backend validates now too;
    // this holds the page's end of it.
    const user = userEvent.setup();
    const assigned: string[] = [];
    const original = Object.getOwnPropertyDescriptor(window, "location");
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...window.location,
        set href(url: string) {
          assigned.push(url);
        },
        get href() {
          return "http://localhost/login";
        },
      },
    });

    try {
      renderLogin("/login?redirect_after=https://evil.example/steal");
      await user.click(await screen.findByTestId("login-oauth-github"));

      expect(assigned).toHaveLength(1);
      const sent = new URL(assigned[0]).searchParams.get("redirect_after");
      expect(sent).not.toContain("evil.example");
      expect(sent).toBe("/");
    } finally {
      if (original) Object.defineProperty(window, "location", original);
    }
  });

  it("sends only the path to the provider, not the query behind it", async () => {
    // Everything in this value is written into the state JWT and shows up
    // in the provider's logs. This application puts audit filters and
    // search terms in query strings.
    const user = userEvent.setup();
    const assigned: string[] = [];
    const original = Object.getOwnPropertyDescriptor(window, "location");
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...window.location,
        set href(url: string) {
          assigned.push(url);
        },
        get href() {
          return "http://localhost/login";
        },
      },
    });

    try {
      renderLogin("/login", { from: "/admin/audit?actor=someone@example.com" });
      await user.click(await screen.findByTestId("login-oauth-github"));

      const sent = new URL(assigned[0]).searchParams.get("redirect_after");
      expect(sent).toBe("/admin/audit");
      expect(sent).not.toContain("someone@example.com");
    } finally {
      if (original) Object.defineProperty(window, "location", original);
    }
  });

  it("returns to the deep link when a live session is already there", async () => {
    // The other way in: a refresh cookie resolves the store to authenticated
    // while /login is mounting, and that path bounced to the dashboard too.
    renderLogin("/login", { from: "/projects/42" });
    useAuthStore.setState({ status: "authenticated" });

    await screen.findByTestId("project-stub");
  });
});
