import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/components/AppProviders";
import { ForgotPasswordPage } from "@/pages/auth/ForgotPasswordPage";
import { ProblemError } from "@/lib/problem";

vi.mock("@/lib/api", () => ({
  postForgotPassword: vi.fn(),
  postResetPassword: vi.fn(),
  postLogin: vi.fn(),
  fetchMe: vi.fn(),
  postLogout: vi.fn(),
  postRegister: vi.fn(),
}));

import { postForgotPassword } from "@/lib/api";
const mockedPostForgot = vi.mocked(postForgotPassword);

function renderForgot() {
  return render(
    <AppProviders router="none">
      <MemoryRouter initialEntries={["/forgot-password"]}>
        <Routes>
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/login" element={<div data-testid="login-stub" />} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  );
}

describe("ForgotPasswordPage", () => {
  beforeEach(() => {
    mockedPostForgot.mockReset();
  });

  it("blocks submit when email is invalid (zod inline error)", async () => {
    const user = userEvent.setup();
    renderForgot();
    await user.type(screen.getByTestId("forgot-email"), "not-an-email");
    await user.click(screen.getByTestId("forgot-submit"));

    expect(await screen.findByText(/valid email/i)).toBeInTheDocument();
    expect(screen.queryByTestId("forgot-success")).not.toBeInTheDocument();
    expect(mockedPostForgot).not.toHaveBeenCalled();
  });

  it("calls the backend and shows the success message on 204", async () => {
    mockedPostForgot.mockResolvedValueOnce(undefined);
    const user = userEvent.setup();
    renderForgot();

    await user.type(screen.getByTestId("forgot-email"), "alice@example.com");
    await user.click(screen.getByTestId("forgot-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("forgot-success")).toBeInTheDocument();
    });
    expect(mockedPostForgot).toHaveBeenCalledWith("alice@example.com");
    // Form is locked after submit so the user can't accidentally re-fire.
    expect(screen.getByTestId("forgot-submit")).toBeDisabled();
    expect(screen.getByTestId("forgot-email")).toBeDisabled();
  });

  it("shows the same success view on backend failure (anti-enumeration)", async () => {
    // Whether the email exists or not, whether the request reached the
    // backend or not, the user MUST see the same confirmation. Otherwise
    // the page is itself an oracle.
    mockedPostForgot.mockRejectedValueOnce(
      new ProblemError("rate limited", {
        status: 429,
        title: "rate_limited",
        detail: "Too many requests",
        problem: null,
      }),
    );

    const user = userEvent.setup();
    renderForgot();
    await user.type(screen.getByTestId("forgot-email"), "alice@example.com");
    await user.click(screen.getByTestId("forgot-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("forgot-success")).toBeInTheDocument();
    });
    // Critical: NO error alert leaks the actual outcome.
    expect(screen.queryByTestId("forgot-error")).not.toBeInTheDocument();
  });

  it("links back to /login", () => {
    renderForgot();
    expect(screen.getByTestId("forgot-back-link")).toHaveAttribute(
      "href",
      "/login",
    );
  });
});
