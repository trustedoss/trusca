/**
 * Provider-level wiring test for the global mutation error toast.
 *
 * The unit contract lives in tests/unit/lib/queryClientErrorToast.test.ts;
 * this test proves the FULL path through the real provider tree instead:
 * AppProviders mounts ToastProvider → ToastProvider registers itself on the
 * toast bus → the MutationCache onError dispatches → the real toast DOM
 * (`[data-testid="admin-toast"]`) appears with the error tone and the
 * locale-independent `mutation-error` key.
 */
import { useMutation } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/components/AppProviders";
import { ProblemError } from "@/lib/problem";

function FailingWrite({ error }: { error: unknown }) {
  const mutation = useMutation({
    mutationFn: () => Promise.reject(error),
  });
  return (
    <button type="button" onClick={() => mutation.mutate()}>
      write
    </button>
  );
}

describe("global mutation error toast (provider wiring)", () => {
  it("renders the real toast when an unhandled mutation fails", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const error = new ProblemError("Scan already queued.", {
      status: 409,
      title: "Conflict",
      detail: "Scan already queued.",
      problem: null,
    });

    render(
      <AppProviders router="none">
        <FailingWrite error={error} />
      </AppProviders>,
    );

    await userEvent.click(screen.getByRole("button", { name: "write" }));

    await waitFor(() => {
      const toast = screen.getByTestId("admin-toast");
      expect(toast).toHaveAttribute("data-tone", "error");
      expect(toast).toHaveAttribute("data-toast-key", "mutation-error");
      expect(toast).toHaveTextContent("Scan already queued.");
    });
  });
});
