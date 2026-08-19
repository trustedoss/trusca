/**
 * ObligationFulfilmentEditor unit tests (N15).
 *
 * The editor is the only writable surface on a screen that is otherwise a
 * catalog, so the tests concentrate on what it sends rather than how it looks:
 *   - the version it opened on rides along as If-Match.
 *   - empty fields are sent as null, not as empty strings.
 *   - a record somebody else owns is not silently taken over.
 *   - a refusal is shown rather than swallowed.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ObligationFulfilmentSummary } from "@/features/projects/api/obligationsApi";
import { ObligationFulfilmentEditor } from "@/features/projects/components/ObligationFulfilmentEditor";
import { ProblemError } from "@/lib/problem";
import { useAuthStore } from "@/stores/authStore";

vi.mock("@/features/projects/api/obligationsApi", async () => ({
  recordObligationFulfilment: vi.fn(),
  clearObligationFulfilment: vi.fn(),
}));

import {
  clearObligationFulfilment,
  recordObligationFulfilment,
} from "@/features/projects/api/obligationsApi";

const mockedRecord = vi.mocked(recordObligationFulfilment);
const mockedClear = vi.mocked(clearObligationFulfilment);

const ME = "11111111-1111-1111-1111-111111111111";
const SOMEBODY_ELSE = "22222222-2222-2222-2222-222222222222";

function existing(
  overrides: Partial<ObligationFulfilmentSummary> = {},
): ObligationFulfilmentSummary {
  return {
    id: "ful-1",
    status: "in_progress",
    assignee_user_id: null,
    due_on: null,
    evidence_note: null,
    evidence_url: null,
    completed_at: null,
    completed_by_user_id: null,
    version: 7,
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

function renderEditor(fulfilment: ObligationFulfilmentSummary | null) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ObligationFulfilmentEditor
        projectId="proj-1"
        obligationId="obg-1"
        fulfilment={fulfilment}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({
    user: {
      id: ME,
      email: "me@example.com",
      displayName: "Me",
      role: "developer",
      isActive: true,
      isSuperuser: false,
      defaultTeamId: null,
      teamIds: [],
    },
  } as never);
  mockedRecord.mockResolvedValue({
    id: "ful-1",
    project_id: "proj-1",
    obligation_id: "obg-1",
    status: "done",
    assignee_user_id: null,
    due_on: null,
    evidence_note: null,
    evidence_url: null,
    completed_at: "2026-08-19T00:00:00Z",
    completed_by_user_id: ME,
    version: 8,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-19T00:00:00Z",
  });
});

describe("ObligationFulfilmentEditor", () => {
  it("sends the version it opened on, so a stale save is refused", async () => {
    // The compliance owner and the release engineer look at the same
    // obligation in the same hour. Without this header the second save
    // silently overwrites the first.
    renderEditor(existing({ version: 7 }));

    await userEvent.click(screen.getByTestId("obligation-fulfilment-save"));

    await waitFor(() => expect(mockedRecord).toHaveBeenCalled());
    expect(mockedRecord.mock.calls[0][2].ifMatchVersion).toBe(7);
  });

  it("sends no version for an obligation nobody has recorded yet", async () => {
    // There is nothing to be stale against, and sending a version the caller
    // invented would make the very first save fail.
    renderEditor(null);

    await userEvent.click(screen.getByTestId("obligation-fulfilment-save"));

    await waitFor(() => expect(mockedRecord).toHaveBeenCalled());
    expect(mockedRecord.mock.calls[0][2].ifMatchVersion).toBeNull();
  });

  it("sends empty fields as null rather than as empty strings", async () => {
    // An empty string is a value: it would store a note that says nothing and
    // a link that goes nowhere, and the drawer would then render both.
    renderEditor(null);

    await userEvent.click(screen.getByTestId("obligation-fulfilment-save"));

    await waitFor(() => expect(mockedRecord).toHaveBeenCalled());
    const sent = mockedRecord.mock.calls[0][2];
    expect(sent.evidence_note).toBeNull();
    expect(sent.evidence_url).toBeNull();
    expect(sent.due_on).toBeNull();
  });

  it("carries what was typed, trimmed", async () => {
    renderEditor(null);

    await userEvent.type(
      screen.getByTestId("obligation-fulfilment-note-input"),
      "  NOTICE shipped in the release archive  ",
    );
    await userEvent.selectOptions(
      screen.getByTestId("obligation-fulfilment-status-select"),
      "done",
    );
    await userEvent.click(screen.getByTestId("obligation-fulfilment-save"));

    await waitFor(() => expect(mockedRecord).toHaveBeenCalled());
    const sent = mockedRecord.mock.calls[0][2];
    expect(sent.status).toBe("done");
    expect(sent.evidence_note).toBe("NOTICE shipped in the release archive");
  });

  it("assigns to the person using it only when they ask", async () => {
    renderEditor(null);

    await userEvent.click(screen.getByTestId("obligation-fulfilment-assign-me"));
    await userEvent.click(screen.getByTestId("obligation-fulfilment-save"));

    await waitFor(() => expect(mockedRecord).toHaveBeenCalled());
    expect(mockedRecord.mock.calls[0][2].assignee_user_id).toBe(ME);
  });

  it("does not take a record away from whoever owns it", async () => {
    // Saving an update to somebody else's row is normal; reassigning it to
    // yourself by touching the note is not, and there is no undo for a name
    // that quietly changed.
    renderEditor(existing({ assignee_user_id: SOMEBODY_ELSE }));

    expect(screen.getByTestId("obligation-fulfilment-assign-me")).toBeDisabled();
    await userEvent.click(screen.getByTestId("obligation-fulfilment-save"));

    await waitFor(() => expect(mockedRecord).toHaveBeenCalled());
    expect(mockedRecord.mock.calls[0][2].assignee_user_id).toBe(SOMEBODY_ELSE);
  });

  it("shows the server's refusal instead of reporting a save that did not happen", async () => {
    // The message is the translated one for the status, not the English
    // `detail` off the wire: this is the moment the reader decides whether to
    // reload, and answering a Korean reader in English is worst here.
    mockedRecord.mockRejectedValueOnce(
      new ProblemError("Precondition Failed", {
        status: 412,
        title: "Precondition Failed",
        detail: "this record changed since you read it; reload and try again",
        problem: null,
      }),
    );
    renderEditor(existing());

    await userEvent.click(screen.getByTestId("obligation-fulfilment-save"));

    expect(
      await screen.findByTestId("obligation-fulfilment-error"),
    ).toHaveTextContent("Refresh to see where things stand");
  });

  it("offers removal only once there is something to remove", async () => {
    const { unmount } = renderEditor(null);
    expect(screen.queryByTestId("obligation-fulfilment-clear")).toBeNull();
    unmount();

    renderEditor(existing());
    await userEvent.click(screen.getByTestId("obligation-fulfilment-clear"));

    await waitFor(() => expect(mockedClear).toHaveBeenCalledWith("proj-1", "obg-1"));
  });
});
