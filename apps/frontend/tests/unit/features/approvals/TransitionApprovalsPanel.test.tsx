/**
 * The transition-approval panel hides itself when the queue is empty, which
 * is right: most deployments never turn the feature on. It used to hide
 * itself on a failed load too, telling an approver nothing was waiting when
 * the list had simply never arrived.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TransitionApprovalsPanel } from "@/features/approvals/TransitionApprovalsPanel";
import { ProblemError } from "@/lib/problem";
import * as transitionApprovalsApi from "@/lib/transitionApprovalsApi";

vi.mock("@/lib/transitionApprovalsApi", async (importOriginal) => {
  const actual = await importOriginal<typeof transitionApprovalsApi>();
  return { ...actual, listPendingTransitions: vi.fn() };
});

const listPending = vi.mocked(transitionApprovalsApi.listPendingTransitions);

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TransitionApprovalsPanel currentUserId="me" />
    </QueryClientProvider>,
  );
}

describe("TransitionApprovalsPanel", () => {
  beforeEach(() => {
    listPending.mockReset();
  });

  it("renders nothing when the queue is empty", async () => {
    listPending.mockResolvedValue({ items: [], total: 0 });

    const { container } = renderPanel();

    await waitFor(() => expect(listPending).toHaveBeenCalled());
    await waitFor(() =>
      expect(
        screen.queryByTestId("transition-approvals-loading"),
      ).not.toBeInTheDocument(),
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a translated error when the queue fails to load", async () => {
    listPending.mockRejectedValue(
      new ProblemError("database unavailable", {
        status: 500,
        title: "Internal Server Error",
        detail: "database unavailable",
        problem: null,
      }),
    );

    renderPanel();

    const alert = await screen.findByTestId("transition-approvals-error");
    expect(alert).toHaveTextContent(
      "The status changes waiting on you could not be loaded.",
    );
    expect(alert).not.toHaveTextContent("database unavailable");
  });
});
