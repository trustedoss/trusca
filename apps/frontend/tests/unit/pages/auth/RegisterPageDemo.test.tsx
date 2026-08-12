/**
 * RegisterPage on the public read-only demo.
 *
 * The demo middleware has no allow-list entry for `POST /auth/register`, so the
 * form could only end in a 403. The page must say so up front and hand over the
 * seeded account instead of letting a visitor fill in three fields first.
 *
 * We mock the wire layer (`@/lib/api`) so `useDemoMode` resolves deterministically
 * and the register/login calls stay inert. Nothing here should reach them.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/components/AppProviders";
import {
  DEMO_LOGIN_EMAIL,
  DEMO_LOGIN_PASSWORD,
} from "@/pages/auth/DemoCredentialsHint";
import { RegisterPage } from "@/pages/auth/RegisterPage";
import { useAuthStore } from "@/stores/authStore";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
  postLogin: vi.fn(),
  postRegister: vi.fn(),
  fetchMe: vi.fn(),
  postLogout: vi.fn(),
}));

import { api, postRegister } from "@/lib/api";

const mockedGet = vi.mocked(api.get);
const mockedPostRegister = vi.mocked(postRegister);

/** Resolve /health with a fixed demo flag. */
function stubHealth(demoReadOnly: boolean) {
  mockedGet.mockResolvedValue({
    data: { status: "ok", demo_read_only: demoReadOnly },
  });
}

function renderRegister() {
  return render(
    <AppProviders router="none">
      <MemoryRouter initialEntries={["/register"]}>
        <Routes>
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/login" element={<div data-testid="login-stub" />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("RegisterPage on the read-only demo", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: null,
      status: "anonymous",
      isAuthenticated: false,
    });
    mockedGet.mockReset();
    mockedPostRegister.mockReset();
  });

  it("replaces the form with the demo notice and the seeded account", async () => {
    stubHealth(true);
    renderRegister();

    expect(await screen.findByTestId("register-demo-notice")).toBeInTheDocument();
    expect(screen.queryByTestId("register-form")).not.toBeInTheDocument();
    expect(screen.getByTestId("register-demo-email")).toHaveTextContent(
      DEMO_LOGIN_EMAIL,
    );
    expect(screen.getByTestId("register-demo-password")).toHaveTextContent(
      DEMO_LOGIN_PASSWORD,
    );
    expect(screen.getByTestId("register-demo-signin")).toHaveAttribute(
      "href",
      "/login",
    );
    expect(mockedPostRegister).not.toHaveBeenCalled();
  });

  it("keeps the normal form on a non-demo deploy and leaks no credentials", async () => {
    stubHealth(false);
    renderRegister();

    expect(await screen.findByTestId("register-form")).toBeInTheDocument();
    expect(screen.queryByTestId("register-demo-notice")).not.toBeInTheDocument();
    expect(screen.queryByText(DEMO_LOGIN_EMAIL)).not.toBeInTheDocument();
    expect(screen.queryByText(DEMO_LOGIN_PASSWORD)).not.toBeInTheDocument();
  });

  it("shows neither form nor notice until /health settles", async () => {
    // A probe that never resolves holds the page in its pre-flag state. The
    // demo image does not set the VITE_DEMO_READ_ONLY build hint, so without
    // this gate the form would paint and then be taken away.
    mockedGet.mockReturnValue(new Promise(() => {}));
    renderRegister();

    expect(await screen.findByTestId("register-resolving")).toBeInTheDocument();
    expect(screen.queryByTestId("register-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("register-demo-notice")).not.toBeInTheDocument();
  });
});
