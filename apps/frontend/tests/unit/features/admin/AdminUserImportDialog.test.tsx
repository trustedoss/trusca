/**
 * The import dialog (N4).
 *
 * Two things are worth pinning here. The file is parsed in the browser and
 * sent as rows, so a malformed header never reaches the API. And the result
 * table lists every row, including the ones that worked, because an import of
 * a few hundred people is where "only failures shown" makes somebody count
 * what is missing.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminUserImportDialog } from "@/features/admin/users/AdminUserImportDialog";

vi.mock("@/features/admin/api/adminUsersApi", () => ({
  bulkCreateAdminUsers: vi.fn(),
  bulkDeactivateAdminUsers: vi.fn(),
}));

import { bulkCreateAdminUsers } from "@/features/admin/api/adminUsersApi";

const mockedBulk = vi.mocked(bulkCreateAdminUsers);

function renderDialog() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AdminUserImportDialog open onOpenChange={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedBulk.mockResolvedValue({
    total: 2,
    succeeded: 1,
    failed: 1,
    results: [
      {
        index: 0,
        identifier: "ada@example.com",
        status: "created",
        user_id: "u-1",
        reason: null,
        detail: null,
      },
      {
        index: 1,
        identifier: "grace@example.com",
        status: "failed",
        user_id: null,
        reason: "email_taken",
        detail: "email already registered",
      },
    ],
  });
});

describe("AdminUserImportDialog", () => {
  it("sends parsed rows rather than the pasted text", async () => {
    renderDialog();

    await userEvent.type(
      screen.getByTestId("admin-user-import-text"),
      "email,role{enter}ada@example.com,viewer",
    );
    await userEvent.click(screen.getByTestId("admin-user-import-submit"));

    await waitFor(() => expect(mockedBulk).toHaveBeenCalled());
    expect(mockedBulk.mock.calls[0][0]).toEqual([
      {
        email: "ada@example.com",
        full_name: null,
        team_id: null,
        role: "viewer",
        password: null,
      },
    ]);
  });

  it("shows every row of the result, not only the failures", async () => {
    renderDialog();

    await userEvent.type(
      screen.getByTestId("admin-user-import-text"),
      "email{enter}ada@example.com{enter}grace@example.com",
    );
    await userEvent.click(screen.getByTestId("admin-user-import-submit"));

    const rows = await screen.findAllByTestId("admin-user-import-row");
    expect(rows.map((row) => row.dataset.status)).toEqual(["created", "failed"]);
  });

  it("translates the refusal token instead of showing the server's English", async () => {
    // The reason is why an administrator will or will not edit their file, and
    // it is the one line on this screen they act on.
    renderDialog();

    await userEvent.type(
      screen.getByTestId("admin-user-import-text"),
      "email{enter}ada@example.com{enter}grace@example.com",
    );
    await userEvent.click(screen.getByTestId("admin-user-import-submit"));

    const rows = await screen.findAllByTestId("admin-user-import-row");
    expect(rows[1]).toHaveTextContent("That address already has an account");
    expect(rows[1]).not.toHaveTextContent("email already registered");
  });

  it("refuses a file with no email column without calling the API", async () => {
    renderDialog();

    await userEvent.type(
      screen.getByTestId("admin-user-import-text"),
      "name,role{enter}Ada,viewer",
    );
    await userEvent.click(screen.getByTestId("admin-user-import-submit"));

    expect(
      await screen.findByTestId("admin-user-import-parse-errors"),
    ).toBeInTheDocument();
    expect(mockedBulk).not.toHaveBeenCalled();
  });

  it("does nothing until there is something to send", () => {
    renderDialog();

    expect(screen.getByTestId("admin-user-import-submit")).toBeDisabled();
  });
});
