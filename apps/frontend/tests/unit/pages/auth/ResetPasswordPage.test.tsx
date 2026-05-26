import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/components/AppProviders";
import { ResetPasswordPage } from "@/pages/auth/ResetPasswordPage";
import { ProblemError } from "@/lib/problem";

vi.mock("@/lib/api", () => ({
  postResetPassword: vi.fn(),
  postForgotPassword: vi.fn(),
  postLogin: vi.fn(),
  fetchMe: vi.fn(),
  postLogout: vi.fn(),
  postRegister: vi.fn(),
}));

import { postResetPassword } from "@/lib/api";
const mockedPostReset = vi.mocked(postResetPassword);

function renderReset(initialPath: string) {
  return render(
    <AppProviders router="none">
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route path="/login" element={<div data-testid="login-stub" />} />
          <Route
            path="/forgot-password"
            element={<div data-testid="forgot-stub" />}
          />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("ResetPasswordPage", () => {
  beforeEach(() => {
    mockedPostReset.mockReset();
  });

  it("renders an invalid-link error when ?token= is missing", () => {
    renderReset("/reset-password");
    expect(screen.getByTestId("reset-invalid-link")).toBeInTheDocument();
    // Form must not appear without a token.
    expect(screen.queryByTestId("reset-form")).not.toBeInTheDocument();
    // Link to /forgot-password is offered as the recovery path.
    expect(screen.getByTestId("reset-forgot-link")).toHaveAttribute(
      "href",
      "/forgot-password",
    );
  });

  it("blocks submit when password is shorter than 8 chars", async () => {
    const user = userEvent.setup();
    renderReset("/reset-password?token=abcd1234");

    await user.type(screen.getByTestId("reset-password"), "short");
    await user.type(screen.getByTestId("reset-confirm"), "short");
    await user.click(screen.getByTestId("reset-submit"));

    // The form-level error message is rendered inside the FormMessage <p>
    // tag with a destructive-text class. We look for that exact copy.
    expect(
      await screen.findByText("Password must be at least 8 characters."),
    ).toBeInTheDocument();
    expect(mockedPostReset).not.toHaveBeenCalled();
  });

  it("flags mismatched passwords inline (no network)", async () => {
    const user = userEvent.setup();
    renderReset("/reset-password?token=abcd1234");

    await user.type(
      screen.getByTestId("reset-password"),
      "longenoughpassword12",
    );
    await user.type(
      screen.getByTestId("reset-confirm"),
      "differentpassword12345",
    );
    await user.click(screen.getByTestId("reset-submit"));

    expect(await screen.findByText(/do not match/i)).toBeInTheDocument();
    expect(mockedPostReset).not.toHaveBeenCalled();
  });

  it("on 204 navigates to /login?registered=1", async () => {
    mockedPostReset.mockResolvedValueOnce(undefined);
    const user = userEvent.setup();
    renderReset("/reset-password?token=valid-token-1234");

    const pwd = "correct-horse-battery";
    await user.type(screen.getByTestId("reset-password"), pwd);
    await user.type(screen.getByTestId("reset-confirm"), pwd);
    await user.click(screen.getByTestId("reset-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("login-stub")).toBeInTheDocument();
    });
    expect(mockedPostReset).toHaveBeenCalledWith("valid-token-1234", pwd);
  });

  it("on 422 (expired) shows the expired error key", async () => {
    mockedPostReset.mockRejectedValueOnce(
      new ProblemError("expired_reset_token", {
        status: 422,
        title: "expired_reset_token",
        detail: "expired_reset_token",
        problem: {
          type: "about:blank",
          title: "expired_reset_token",
          status: 422,
          detail: "expired_reset_token",
        },
      }),
    );

    const user = userEvent.setup();
    renderReset("/reset-password?token=expired-token-99");

    const pwd = "correct-horse-battery";
    await user.type(screen.getByTestId("reset-password"), pwd);
    await user.type(screen.getByTestId("reset-confirm"), pwd);
    await user.click(screen.getByTestId("reset-submit"));

    const alert = await screen.findByTestId("reset-error");
    expect(alert).toHaveTextContent(/expired/i);
  });

  it("on 422 (invalid) shows the invalid error key", async () => {
    mockedPostReset.mockRejectedValueOnce(
      new ProblemError("invalid_reset_token", {
        status: 422,
        title: "invalid_reset_token",
        detail: "invalid_reset_token",
        problem: {
          type: "about:blank",
          title: "invalid_reset_token",
          status: 422,
          detail: "invalid_reset_token",
        },
      }),
    );

    const user = userEvent.setup();
    renderReset("/reset-password?token=stale");

    const pwd = "correct-horse-battery";
    await user.type(screen.getByTestId("reset-password"), pwd);
    await user.type(screen.getByTestId("reset-confirm"), pwd);
    await user.click(screen.getByTestId("reset-submit"));

    const alert = await screen.findByTestId("reset-error");
    expect(alert).toHaveTextContent(/invalid|already been used/i);
  });

  it("on transport failure surfaces a network error", async () => {
    mockedPostReset.mockRejectedValueOnce(
      new ProblemError("network", {
        status: 0,
        title: "network",
        detail: "network",
        problem: null,
      }),
    );

    const user = userEvent.setup();
    renderReset("/reset-password?token=t");

    const pwd = "correct-horse-battery";
    await user.type(screen.getByTestId("reset-password"), pwd);
    await user.type(screen.getByTestId("reset-confirm"), pwd);
    await user.click(screen.getByTestId("reset-submit"));

    const alert = await screen.findByTestId("reset-error");
    expect(alert).toHaveTextContent(/network/i);
  });
});
