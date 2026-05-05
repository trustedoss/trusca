import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/App";
import { AppProviders } from "@/components/AppProviders";
import { useAuthStore, type AuthUser } from "@/stores/authStore";

vi.mock("@/lib/api", () => ({
  fetchMe: vi.fn(),
  postLogin: vi.fn(),
  postRegister: vi.fn(),
  postLogout: vi.fn(),
}));

const fakeUser: AuthUser = {
  id: "u-1",
  email: "alice@example.com",
  displayName: "Alice",
  role: "developer",
  isActive: true,
  isSuperuser: false,
  teamId: null,
};

function renderAppAt(path: string) {
  window.history.replaceState(null, "", path);
  return render(
    <AppProviders>
      <App />
    </AppProviders>,
  );
}

describe("App smoke (authenticated home)", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: fakeUser,
      accessToken: "tok-app",
      status: "authenticated",
      isAuthenticated: true,
    });
  });
  afterEach(() => {
    useAuthStore.getState().reset();
    window.history.replaceState(null, "", "/");
  });

  it("mounts the home page with the bootstrap title (EN by default)", async () => {
    renderAppAt("/");

    await waitFor(() => {
      expect(screen.getByTestId("home-main")).toBeInTheDocument();
    });
    expect(screen.getByTestId("home-title")).toHaveTextContent(
      /Welcome to TrustedOSS Portal/i,
    );
  });

  it("renders the 5 risk severity tokens once each", async () => {
    renderAppAt("/");
    await waitFor(() => {
      expect(screen.getByTestId("risk-legend")).toBeInTheDocument();
    });

    const legend = screen.getByTestId("risk-legend");
    const items = legend.querySelectorAll("[data-risk]");
    expect(items).toHaveLength(5);

    const severities = Array.from(items).map((node) =>
      node.getAttribute("data-risk"),
    );
    expect(severities).toEqual(["critical", "high", "medium", "low", "info"]);
  });

  it("toggles the active language between en and ko", async () => {
    const user = userEvent.setup();
    renderAppAt("/");
    await waitFor(() => {
      expect(screen.getByTestId("home-main")).toBeInTheDocument();
    });

    const toggle = screen.getByTestId("language-toggle");
    expect(toggle).toHaveAttribute("data-current-language", "en");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("data-current-language", "ko");
    expect(screen.getByTestId("home-title")).toHaveTextContent(
      /TrustedOSS Portal에 오신 것을 환영합니다/,
    );

    await user.click(toggle);
    expect(toggle).toHaveAttribute("data-current-language", "en");
  });

  it("renders the logout stub button", async () => {
    renderAppAt("/");
    await waitFor(() => {
      expect(screen.getByTestId("logout-button")).toBeInTheDocument();
    });
  });
});

describe("App smoke (unauthenticated)", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: null,
      status: "anonymous",
      isAuthenticated: false,
    });
  });
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("redirects unauthenticated visitors from / to /login", async () => {
    renderAppAt("/");
    await waitFor(() => {
      expect(screen.getByTestId("login-page")).toBeInTheDocument();
    });
  });

  it("falls through unknown paths to /login", async () => {
    renderAppAt("/this-route-does-not-exist");
    await waitFor(() => {
      expect(screen.getByTestId("login-page")).toBeInTheDocument();
    });
  });
});
