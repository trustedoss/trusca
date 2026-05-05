import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";

import { AppProviders } from "@/components/AppProviders";
import { ForgotPasswordPage } from "@/pages/auth/ForgotPasswordPage";

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
  let fetchSpy: MockInstance;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });
  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("blocks submit when email is invalid (zod inline error)", async () => {
    const user = userEvent.setup();
    renderForgot();
    await user.type(screen.getByTestId("forgot-email"), "not-an-email");
    await user.click(screen.getByTestId("forgot-submit"));

    expect(await screen.findByText(/valid email/i)).toBeInTheDocument();
    expect(screen.queryByTestId("forgot-success")).not.toBeInTheDocument();
  });

  it("shows the stub success message on submit (no API call yet)", async () => {
    const user = userEvent.setup();
    renderForgot();
    await user.type(screen.getByTestId("forgot-email"), "alice@example.com");
    await user.click(screen.getByTestId("forgot-submit"));

    expect(await screen.findByTestId("forgot-success")).toHaveTextContent(
      /admin will reach out/i,
    );
    // Critical guard: this PR must NOT hit the network — the endpoint lands in PR #18.
    expect(fetchSpy).not.toHaveBeenCalled();
    // Form is locked after submit so the user can't accidentally re-fire.
    expect(screen.getByTestId("forgot-submit")).toBeDisabled();
    expect(screen.getByTestId("forgot-email")).toBeDisabled();
  });

  it("links back to /login", () => {
    renderForgot();
    expect(screen.getByTestId("forgot-back-link")).toHaveAttribute(
      "href",
      "/login",
    );
  });
});
