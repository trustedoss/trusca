/**
 * A screen for a feature most deployments will not turn on.
 *
 * The first test is the important one. Somebody arriving on a deployment that
 * does not use this must be told so, not shown an empty queue: an empty queue
 * reads as "nobody has asked yet", and they would file a request into a
 * surface that answers 404 and then wait for an answer nothing recorded.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IntakeRequestsPage } from "@/features/intake/IntakeRequestsPage";

const listIntakeRequests = vi.fn();
const openIntakeRequest = vi.fn();
const transitionIntakeRequest = vi.fn();
const useDeploymentFeatures = vi.fn();

vi.mock("@/lib/intakeRequestsApi", () => ({
  listIntakeRequests: (...args: unknown[]) => listIntakeRequests(...args),
  openIntakeRequest: (...args: unknown[]) => openIntakeRequest(...args),
  transitionIntakeRequest: (...args: unknown[]) =>
    transitionIntakeRequest(...args),
}));

vi.mock("@/features/about/api/useDeploymentFeatures", () => ({
  useDeploymentFeatures: () => useDeploymentFeatures(),
}));

vi.mock("react-i18next", () => ({
  // The i18n instance too: RelativeTime reads resolvedLanguage to format, and
  // a mock with only `t` fails inside a child rather than where it was written.
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en", language: "en" },
  }),
}));

function row(overrides: Record<string, unknown> = {}) {
  return {
    id: "r-1",
    project_id: "p-1",
    team_id: "t-1",
    purl: "pkg:npm/lodash",
    justification: "we need a date library and this one is maintained",
    status: "pending",
    requested_by_user_id: "u-1",
    decided_by_user_id: null,
    decision_note: null,
    decided_at: null,
    version: 1,
    created_at: "2026-08-19T00:00:00Z",
    updated_at: "2026-08-19T00:00:00Z",
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <IntakeRequestsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("IntakeRequestsPage", () => {
  beforeEach(() => {
    listIntakeRequests.mockReset();
    openIntakeRequest.mockReset();
    transitionIntakeRequest.mockReset();
    useDeploymentFeatures.mockReset();
  });

  it("says the deployment does not use this, rather than showing an empty queue", async () => {
    useDeploymentFeatures.mockReturnValue({});

    renderPage();

    expect(screen.getByTestId("intake-disabled")).toBeInTheDocument();
    expect(screen.queryByTestId("intake-ask-form")).not.toBeInTheDocument();
    // And it does not go asking the server about a surface that is not there.
    expect(listIntakeRequests).not.toHaveBeenCalled();
  });

  it("shows the queue where the deployment turned it on", async () => {
    useDeploymentFeatures.mockReturnValue({ intake_requests: true });
    listIntakeRequests.mockResolvedValue({ items: [row()], total: 1 });

    renderPage();

    expect(await screen.findByTestId("intake-row-r-1")).toBeInTheDocument();
    expect(screen.queryByTestId("intake-disabled")).not.toBeInTheDocument();
  });

  it("asks with what was typed in", async () => {
    useDeploymentFeatures.mockReturnValue({ intake_requests: true });
    listIntakeRequests.mockResolvedValue({ items: [], total: 0 });
    openIntakeRequest.mockResolvedValue(row());
    renderPage();

    await userEvent.type(await screen.findByTestId("intake-project"), "p-1");
    await userEvent.type(screen.getByTestId("intake-purl"), "pkg:npm/lodash");
    await userEvent.type(
      screen.getByTestId("intake-justification"),
      "we need a date library",
    );
    await userEvent.click(screen.getByTestId("intake-ask"));

    await waitFor(() => expect(openIntakeRequest).toHaveBeenCalled());
    expect(openIntakeRequest.mock.calls[0][0]).toMatchObject({
      project_id: "p-1",
      purl: "pkg:npm/lodash",
    });
  });

  it("carries the version so two reviewers cannot lose a decision", async () => {
    useDeploymentFeatures.mockReturnValue({ intake_requests: true });
    listIntakeRequests.mockResolvedValue({
      items: [row({ status: "under_review", version: 4 })],
      total: 1,
    });
    transitionIntakeRequest.mockResolvedValue(row({ status: "approved" }));
    renderPage();

    await userEvent.click(await screen.findByTestId("intake-approve-r-1"));

    await waitFor(() => expect(transitionIntakeRequest).toHaveBeenCalled());
    expect(transitionIntakeRequest.mock.calls[0][2]).toBe(4);
  });

  it("offers no buttons on a request that has been answered", async () => {
    useDeploymentFeatures.mockReturnValue({ intake_requests: true });
    listIntakeRequests.mockResolvedValue({
      items: [row({ status: "approved" })],
      total: 1,
    });

    renderPage();

    await screen.findByTestId("intake-row-r-1");
    expect(screen.queryByTestId("intake-approve-r-1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("intake-reject-r-1")).not.toBeInTheDocument();
  });
});
